"""대화 이력을 세션 사이에 넘겨주는 백엔드.

한 대화(세션)가 끝나면 여기로 저장하고, 다음 대화를 시작할 때 여기서
불러온다. 실제 Spring 서버가 아직 없거나 붙일 수 없는 상황에서도 개발/CLI
테스트가 가능하도록 로컬 파일 백엔드를 기본값으로 둔다.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Optional, Protocol

logger = logging.getLogger(__name__)


class ConversationBackend(Protocol):
    def load(self, session_id: str) -> list[dict[str, Any]]:
        """세션의 지난 대화(user/assistant 턴 목록)를 불러온다. 없으면 빈 리스트."""

    def save(self, session_id: str, messages: list[dict[str, Any]]) -> None:
        """세션의 대화 전체를 덮어쓴다."""


class FileConversationBackend:
    """서버 없이 쓰는 로컬 JSON 백엔드. 세션ID -> 메시지 목록."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _read_all(self) -> dict[str, list[dict[str, Any]]]:
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except json.JSONDecodeError:
            return {}

    def load(self, session_id: str) -> list[dict[str, Any]]:
        return self._read_all().get(session_id, [])

    def save(self, session_id: str, messages: list[dict[str, Any]]) -> None:
        data = self._read_all()
        data[session_id] = messages
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


class HttpConversationBackend:
    """실제 백엔드(Spring 등)에 REST로 대화를 저장/조회.

    기대하는 최소 REST 계약 (백엔드팀과 맞출 값):
      GET {base_url}/conversations/{session_id}
        -> 200 {"messages": [{"role": "user"|"assistant", "content": "..."}]}
        -> 404 (대화 없음, 빈 목록으로 취급)
      PUT {base_url}/conversations/{session_id}
        body {"messages": [...]}
        -> 200/204

    네트워크/서버 오류는 예외를 던지지 않고 로그만 남긴다 — 로봇 채팅
    기능이 백엔드 장애 때문에 멈추면 안 되기 때문 (client.py의 견고성
    원칙과 동일).
    """

    def __init__(self, base_url: str, *, timeout: float = 10.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def load(self, session_id: str) -> list[dict[str, Any]]:
        url = f"{self.base_url}/conversations/{session_id}"
        req = urllib.request.Request(url, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return data.get("messages", [])
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return []
            logger.warning(f"대화 이력 조회 실패 (HTTP {exc.code}) - 새 대화로 시작합니다.")
            return []
        except urllib.error.URLError as exc:
            logger.warning(f"대화 백엔드 연결 실패 ({exc.reason}) - 새 대화로 시작합니다.")
            return []

    def save(self, session_id: str, messages: list[dict[str, Any]]) -> None:
        url = f"{self.base_url}/conversations/{session_id}"
        body = json.dumps({"messages": messages}, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="PUT",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                resp.read()
        except (urllib.error.HTTPError, urllib.error.URLError) as exc:
            logger.warning(
                f"대화 저장 실패 - 이번 대화는 로컬 메모리에만 남고 서버엔 저장되지 않았습니다: {exc}"
            )


def create_conversation_backend(
    config: dict[str, Any], *, default_dir: Optional[Path] = None
) -> ConversationBackend:
    conv = config.get("conversation") or {}
    kind = str(conv.get("backend", "file")).lower()

    if kind == "http":
        base_url = os.environ.get("CONVERSATION_BASE_URL") or conv.get("base_url")
        if not base_url:
            raise ValueError(
                "conversation.backend=http 인데 base_url이 없습니다. "
                "config의 conversation.base_url 또는 CONVERSATION_BASE_URL 환경변수를 설정하세요."
            )
        timeout = float(
            os.environ.get("CONVERSATION_TIMEOUT_SECONDS") or conv.get("timeout_seconds", 10.0)
        )
        return HttpConversationBackend(str(base_url), timeout=timeout)

    file_path = conv.get("file_path")
    if file_path:
        path = Path(file_path)
    elif default_dir is not None:
        path = Path(default_dir) / "conversation_history.json"
    else:
        path = Path("conversation_history.json")
    return FileConversationBackend(path)
