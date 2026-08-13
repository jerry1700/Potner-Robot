"""웹 챗(plant-robot-chat)의 LLM을 HTTP로 빌려 쓰는 어댑터.

plant-robot-chat의 `/api/chat`은 무상태(stateless)라 히스토리를 매 요청에
동봉해야 한다. 이 어댑터가 세션별 히스토리·요약을 서버 측에서 관리하므로,
휴대폰 음성과 모니터 타이핑이 같은 session_id를 쓰면 하나의 대화로 이어진다.

주의:
- `Origin` 헤더를 붙이지 말 것 — 웹 챗의 isAllowedOrigin()은 Origin이 없으면
  통과시키고, 있으면 Host와 일치해야 해서 403이 난다.
- 웹 챗 서버는 최근 20턴(40메시지)만 사용하므로, 넘치는 분량은
  `/api/summarize`로 압축해 summary로 넘긴다 (웹 챗 프론트와 동일한 전략).
"""

from __future__ import annotations

import json
import logging
import os
import threading
import uuid
from typing import Optional

import requests

logger = logging.getLogger("voice_chat.webchat")

DEFAULT_BASE_URL = "http://127.0.0.1:3000"

# 웹 챗 서버가 히스토리를 최근 20턴만 쓰므로(route.ts) 그 경계에서 요약한다.
MAX_HISTORY_MESSAGES = 40

CONNECT_TIMEOUT = 5.0
READ_TIMEOUT = 90.0  # tool use 왕복(최대 5라운드)까지 감안

MSG_UNREACHABLE = (
    "초록이 두뇌(웹 챗 서버)에 연결할 수 없어요. "
    "노트북에서 웹 챗(npm run dev)이 켜져 있는지 확인해 주세요."
)
MSG_LLM_ERROR = "초록이가 잠깐 딴생각을 했어요. 다시 한번 말해줄래요?"


class _Session:
    """세션 하나의 대화 상태. lock은 음성/타이핑 동시 턴의 히스토리 경합 방지."""

    def __init__(self) -> None:
        self.conversation_id = str(uuid.uuid4())
        self.history: list[dict[str, str]] = []
        self.summary: str = ""
        self.lock = threading.Lock()


class WebChatLLM:
    """세션별 히스토리를 들고 plant-robot-chat `/api/chat`을 호출한다."""

    def __init__(self, base_url: Optional[str] = None) -> None:
        self.base_url = (
            base_url or os.environ.get("WEBCHAT_URL") or DEFAULT_BASE_URL
        ).rstrip("/")
        self._sessions: dict[str, _Session] = {}
        self._lock = threading.Lock()

    # --- 공개 API ---

    def chat_once(self, session_id: str, message: str) -> str:
        """질문 → 초록이 응답. 예외를 던지지 않고 항상 텍스트를 돌려준다."""
        return "".join(self.chat_stream(session_id, message))

    def chat_stream(self, session_id: str, message: str):
        """텍스트 델타를 흘려보내는 제너레이터 (음성 파이프라이닝용).

        예외를 밖으로 던지지 않는다 — 아무것도 못 받았으면 폴백 문구를 yield.
        성공 시(부분 성공 포함) 히스토리를 갱신하고, 폴백 턴은 기록하지 않는다.
        """
        session = self._session(session_id)
        with session.lock:
            self._maybe_summarize(session)
            parts: list[str] = []
            try:
                for delta in self._stream_chat(session, message):
                    parts.append(delta)
                    yield delta
            except requests.exceptions.ConnectionError:
                logger.error(f"웹 챗 서버 연결 실패: {self.base_url}")
            except Exception as exc:  # noqa: BLE001 — 어떤 실패든 대화는 계속돼야 한다
                logger.error(f"웹 챗 LLM 호출 실패: {exc}")
            reply = "".join(parts).strip()
            if reply:
                session.history.append({"role": "user", "content": message})
                session.history.append({"role": "assistant", "content": reply})
        if not reply:
            # 폴백 판별의 healthy()는 죽은 서버에 최대 5초가 걸린다 — lock
            # 밖에서 해야 같은 세션의 다음 턴(모니터 타이핑 등)이 헬스체크
            # 동안 같이 막히지 않는다.
            yield MSG_UNREACHABLE if not self.healthy() else MSG_LLM_ERROR

    def end_session(self, session_id: str) -> bool:
        with self._lock:
            return self._sessions.pop(session_id, None) is not None

    def healthy(self) -> bool:
        try:
            requests.get(self.base_url, timeout=CONNECT_TIMEOUT)
            return True
        except requests.exceptions.RequestException:
            return False

    # --- 내부 ---

    def _session(self, session_id: str) -> _Session:
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                session = _Session()
                self._sessions[session_id] = session
            return session

    def _stream_chat(self, session: _Session, message: str):
        """`/api/chat`을 호출해 text_delta를 도착하는 대로 yield한다."""
        payload = {
            "message": message,
            "history": session.history,
            "conversationId": session.conversation_id,
        }
        if session.summary:
            payload["summary"] = session.summary

        resp = requests.post(
            f"{self.base_url}/api/chat",
            json=payload,
            stream=True,
            timeout=(CONNECT_TIMEOUT, READ_TIMEOUT),
        )
        try:
            if resp.status_code != 200:
                # 스트림 열기 전 실패는 SSE가 아니라 JSON 에러 바디로 온다.
                try:
                    detail = resp.json().get("message", "")
                except ValueError:
                    detail = resp.text[:200]
                raise RuntimeError(f"/api/chat {resp.status_code}: {detail}")

            event = ""
            for line in resp.iter_lines(decode_unicode=True):
                if line is None:
                    continue
                if line == "":  # 프레임 경계
                    event = ""
                    continue
                if line.startswith("event:"):
                    event = line[len("event:"):].strip()
                elif line.startswith("data:"):
                    try:
                        data = json.loads(line[len("data:"):].strip() or "{}")
                    except ValueError:
                        continue
                    if event == "text_delta":
                        text = data.get("text", "")
                        if text:
                            yield text
                    elif event == "error":
                        raise RuntimeError(data.get("message", "SSE error"))
                    elif event == "message_complete":
                        return
        finally:
            # stream=True 응답은 명시적으로 닫아야 커넥션이 풀로 반환된다.
            # message_complete 조기 반환·에러·정상 소진 어느 경로든 여기로 온다.
            resp.close()

    def _maybe_summarize(self, session: _Session) -> None:
        """히스토리가 20턴을 넘으면 오래된 턴을 summary로 접는다.

        요약이 **성공했을 때만** 히스토리를 자른다. 실패했는데 잘라버리면
        옛 턴이 요약도 없이 유실된다. 자르지 않고 두는 것은 무해하다 —
        웹 챗 서버가 어차피 최근 20턴만 쓰고, 다음 턴에 다시 시도한다.
        """
        if len(session.history) <= MAX_HISTORY_MESSAGES:
            return
        old = session.history[:-MAX_HISTORY_MESSAGES]
        try:
            resp = requests.post(
                f"{self.base_url}/api/summarize",
                json={"messages": old, "previousSummary": session.summary},
                timeout=(CONNECT_TIMEOUT, READ_TIMEOUT),
            )
            summary = resp.json().get("summary", "")
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"요약 실패 (자르지 않고 다음 턴에 재시도): {exc}")
            return
        if not summary:
            logger.warning("요약이 비어 있음 — 자르지 않고 다음 턴에 재시도")
            return
        session.summary = summary
        session.history = session.history[-MAX_HISTORY_MESSAGES:]
