"""오린카 음성 대화 로컬 서버.

휴대폰 브라우저(같은 Wi-Fi)에서 로봇 IP:PORT로 접속해 음성으로 대화한다.

    녹음 업로드 → STT(whisper-1) → potner_llm DialogueService(tool use 포함)
    → TTS(gpt-4o-mini-tts) → mp3 반환

실행 (레포 루트에서):
    python -m uvicorn app:app --app-dir voice-chat-server --host 0.0.0.0 --port 8080
또는:
    python voice-chat-server/app.py
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import random
import re
import sys
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, AsyncIterator, Iterator, Optional

import yaml
from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

REPO_ROOT = Path(__file__).resolve().parents[1]
# colcon install 없이도 potner_llm을 import할 수 있게 경로를 올린다 (tests/conftest.py와 동일).
sys.path.insert(0, str(REPO_ROOT / "src" / "potner_llm"))

from potner_llm.client import describe_llm  # noqa: E402
from potner_llm.conversation_backend import create_conversation_backend  # noqa: E402
from potner_llm.dialogue import DialogueService  # noqa: E402
from potner_llm.events import EventStore  # noqa: E402
from potner_llm.status import MetricLevel, PlantStatus  # noqa: E402

from local_stt import LocalSttClient  # noqa: E402
from speech import SpeechApiError, SpeechClient  # noqa: E402
from webchat_llm import WebChatLLM  # noqa: E402

logger = logging.getLogger("voice_chat")

STATIC_DIR = Path(__file__).resolve().parent / "static"
LLM_PACKAGE_DIR = REPO_ROOT / "src" / "potner_llm"
MAX_AUDIO_BYTES = 15 * 1024 * 1024  # 휴대폰 녹음 1~2분이면 충분

MSG_NO_SPEECH = "잘 못 들었어요. 조금 더 크게 말해줄래요?"
MSG_STT_ERROR = "귀가 잠깐 먹먹했어요. 다시 한번 말해줄래요?"
MSG_AUDIO_TOO_BIG = "녹음이 너무 길어요. 짧게 나눠서 말해줄래요?"

# 업로드 Content-Type → whisper에 넘길 파일 확장자
_AUDIO_EXTENSIONS = {
    "audio/webm": "webm",
    "audio/ogg": "ogg",
    "audio/mp4": "mp4",
    "audio/mpeg": "mp3",
    "audio/wav": "wav",
    "audio/x-wav": "wav",
    "audio/flac": "flac",
}


def _load_env_file(path: Path) -> None:
    """potner_llm cli.py와 동일 — python-dotenv 의존 없이 .env를 직접 읽는다."""
    import os

    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if value and key not in os.environ:
            os.environ[key] = value


def _demo_status() -> PlantStatus:
    """실제 센서 노드 연결 전 테스트용 고정 상태 (cli.py와 동일).

    로봇에 올릴 때는 MQTT/ROS에서 최신 상태를 읽는 provider로 교체한다.
    """
    ok = MetricLevel(name="ok", value=25.0, level="normal", label_ko="적정")
    dry = MetricLevel(name="soil", value=18.0, level="low", label_ko="건조")
    return PlantStatus(
        timestamp="2026-07-29T10:00:00",
        soil=dry,
        temperature=ok,
        humidity=ok,
        light=ok,
        summary_ko="토양이 건조해서 물이 필요해요",
        needs_attention=True,
    )


class TurnBroadcaster:
    """대화 턴을 SSE 구독자(모니터 페이지)에게 실시간으로 중계한다.

    구독자마다 asyncio.Queue 하나 — 이벤트 루프 안에서만 publish/subscribe하므로
    별도 잠금이 필요 없다. 느린 구독자는 큐가 차면 이벤트를 버린다(모니터 용도라 무손실 불필요).
    """

    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue] = set()

    def publish(self, event: dict[str, Any]) -> None:
        for queue in self._subscribers:
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                pass

    async def subscribe(self, request: Request) -> AsyncIterator[str]:
        queue: asyncio.Queue = asyncio.Queue(maxsize=64)
        self._subscribers.add(queue)
        try:
            while True:
                if await request.is_disconnected():
                    return
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15.0)
                except asyncio.TimeoutError:
                    yield ": ping\n\n"  # keep-alive (프록시/브라우저 유휴 타임아웃 방지)
                    continue
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        finally:
            self._subscribers.discard(queue)


class VoiceChatApp:
    """세션별 DialogueService와 SpeechClient를 관리한다."""

    def __init__(self) -> None:
        import os

        _load_env_file(REPO_ROOT / ".env")
        config_path = LLM_PACKAGE_DIR / "config" / "llm.yaml"
        self.config: dict[str, Any] = (
            yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        )

        api_key = (
            os.environ.get("OPENAI_API_KEY") or os.environ.get("GMS_API_KEY") or ""
        ).strip()
        if not api_key:
            raise RuntimeError(".env에 GMS_API_KEY(또는 OPENAI_API_KEY)가 필요합니다")
        self.speech = SpeechClient(api_key=api_key)

        self._event_store = EventStore(LLM_PACKAGE_DIR / "data" / "events.jsonl")
        self._backend = create_conversation_backend(
            self.config, default_dir=LLM_PACKAGE_DIR / "data"
        )
        self._sessions: dict[str, DialogueService] = {}
        self._lock = threading.Lock()

        # LLM 두뇌 선택 — webchat: plant-robot-chat의 초록이(Claude) 빌려 쓰기(기본),
        # potner: 기존 potner_llm DialogueService.
        self.llm_backend = os.environ.get("LLM_BACKEND", "webchat").strip().lower()
        if self.llm_backend not in ("webchat", "potner"):
            raise RuntimeError(f"LLM_BACKEND는 webchat|potner 중 하나여야 합니다: {self.llm_backend}")
        self.webchat = WebChatLLM() if self.llm_backend == "webchat" else None

        # STT 백엔드 — local: faster-whisper(GPU), gms: whisper-1 API.
        # local이어도 모델 로드 완료 전/실패 시에는 GMS로 자동 폴백한다.
        self.stt_backend = os.environ.get("STT_BACKEND", "local").strip().lower()
        if self.stt_backend not in ("local", "gms"):
            raise RuntimeError(f"STT_BACKEND는 local|gms 중 하나여야 합니다: {self.stt_backend}")
        self.local_stt = LocalSttClient() if self.stt_backend == "local" else None

    def transcribe(self, audio: bytes, *, filename: str, content_type: str) -> str:
        """로컬 whisper 우선, 준비 전이거나 실패하면 GMS whisper-1 폴백."""
        if self.local_stt is not None and self.local_stt.ready:
            try:
                return self.local_stt.transcribe(
                    audio, filename=filename, content_type=content_type
                )
            except Exception as exc:  # noqa: BLE001 — 로컬 실패가 대화를 끊으면 안 된다
                logger.error("로컬 STT 실패, GMS로 폴백: %s", exc)
        return self.speech.transcribe(
            audio, filename=filename, content_type=content_type
        )

    def reply(self, session_id: str, user_text: str) -> str:
        """선택된 두뇌로 응답 생성. 어느 쪽이든 예외 없이 항상 텍스트를 돌려준다."""
        if self.webchat is not None:
            return self.webchat.chat_once(session_id, user_text)
        return self.dialogue(session_id).chat_once(user_text)

    def reply_stream(self, session_id: str, user_text: str) -> Iterator[str]:
        """응답을 텍스트 델타로 흘려보낸다 (potner 백엔드는 통짜 한 덩어리)."""
        if self.webchat is not None:
            yield from self.webchat.chat_stream(session_id, user_text)
        else:
            yield self.dialogue(session_id).chat_once(user_text)

    def dialogue(self, session_id: str) -> DialogueService:
        """세션별 DialogueService (지난 대화가 있으면 백엔드에서 복원)."""
        with self._lock:
            service = self._sessions.get(session_id)
            if service is None:
                service = DialogueService(
                    self.config,
                    get_status=_demo_status,
                    event_store=self._event_store,
                    conversation_backend=self._backend,
                    session_id=session_id,
                )
                self._sessions[session_id] = service
            return service

    def end_session(self, session_id: str) -> bool:
        ended = False
        if self.webchat is not None:
            ended = self.webchat.end_session(session_id)
        with self._lock:
            service = self._sessions.pop(session_id, None)
        if service is not None:
            service.end_conversation()  # 히스토리 저장 후 비움
            ended = True
        return ended


logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
state = VoiceChatApp()
broadcaster = TurnBroadcaster()
app = FastAPI(title="오린카 음성 대화")

def _describe_backend() -> str:
    if state.webchat is not None:
        alive = "연결됨" if state.webchat.healthy() else "⚠️ 응답 없음 — npm run dev 확인"
        return f"LLM 백엔드: webchat({state.webchat.base_url}, 초록이/Claude) — {alive}"
    return describe_llm(state.dialogue("voice").llm, state.config).splitlines()[0]


logger.info(_describe_backend())


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/monitor")
def monitor() -> FileResponse:
    """노트북 등에서 대화를 실시간으로 지켜보는 모니터 페이지."""
    return FileResponse(STATIC_DIR / "monitor.html")


@app.get("/api/events")
async def events(request: Request) -> StreamingResponse:
    """대화 턴 SSE 스트림 — /monitor 페이지가 구독한다."""
    return StreamingResponse(
        broadcaster.subscribe(request),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {"status": "ok", "llm": _describe_backend()}


@app.post("/api/voice-chat")
async def voice_chat(
    audio: UploadFile = File(...),
    session_id: str = Form("voice"),
) -> JSONResponse:
    """음성 파일 → (STT → LLM → TTS) → 응답 텍스트 + mp3(base64)."""
    started = time.monotonic()

    blob = await audio.read()
    if len(blob) > MAX_AUDIO_BYTES:
        return _error(413, MSG_AUDIO_TOO_BIG)
    if not blob:
        return _error(400, MSG_NO_SPEECH)

    content_type = (audio.content_type or "audio/webm").split(";")[0].strip()
    extension = _AUDIO_EXTENSIONS.get(content_type, "webm")

    # a. STT
    try:
        user_text = state.transcribe(
            blob, filename=f"speech.{extension}", content_type=content_type
        )
    except SpeechApiError as exc:
        logger.error("STT 실패: %s", exc)
        return _error(502, MSG_STT_ERROR)
    if not user_text:
        return _error(422, MSG_NO_SPEECH)

    # b. LLM — 어느 백엔드든 예외 없이 항상 텍스트를 돌려준다.
    reply_text = state.reply(session_id, user_text)

    # c. TTS — 실패해도 텍스트는 돌려준다 (화면 표시는 가능하게)
    audio_b64: Optional[str] = None
    try:
        audio_b64 = base64.b64encode(
            state.speech.synthesize(_tts_text(reply_text))
        ).decode("ascii")
    except SpeechApiError as exc:
        logger.error("TTS 실패 (텍스트만 반환): %s", exc)

    elapsed_ms = round((time.monotonic() - started) * 1000)
    logger.info("voice-chat 완료 session=%s %dms", session_id, elapsed_ms)

    # d. 모니터 페이지로 중계 (오디오는 무거워서 텍스트만)
    broadcaster.publish(
        {
            "session_id": session_id,
            "source": "voice",
            "user_text": user_text,
            "reply_text": reply_text,
            "elapsed_ms": elapsed_ms,
        }
    )

    # e. 응답
    return JSONResponse(
        {
            "success": True,
            "session_id": session_id,
            "user_text": user_text,
            "reply_text": reply_text,
            "audio_b64": audio_b64,
            "audio_mime": "audio/mpeg" if audio_b64 else None,
            "elapsed_ms": elapsed_ms,
        }
    )


@app.post("/api/text-chat")
async def text_chat(request: Request) -> JSONResponse:
    """타이핑 입력 (모니터 페이지) — 음성과 같은 세션 히스토리를 탄다."""
    body = await request.json()
    text = str(body.get("text") or "").strip()
    session_id = str(body.get("session_id") or "voice-web")
    client_id = str(body.get("client_id") or "")
    if not text:
        return _error(400, "메시지가 비어 있어요.")
    if len(text) > 4000:  # 웹 챗 입력 상한과 동일
        return _error(413, "메시지가 너무 길어요.")

    started = time.monotonic()
    first_delta_time: Optional[float] = None
    parts: list[str] = []
    for delta in state.reply_stream(session_id, text):
        if first_delta_time is None:
            first_delta_time = time.monotonic()
        parts.append(delta)
    reply_text = "".join(parts).strip()
    elapsed_ms = round((time.monotonic() - started) * 1000)
    # 타이핑은 STT 구간이 없으니 "질문 종료→답변 시작"은 곧 첫 델타 도착 시간.
    response_start_ms = (
        round((first_delta_time - started) * 1000) if first_delta_time else None
    )
    logger.info("text-chat 완료 session=%s %dms", session_id, elapsed_ms)

    broadcaster.publish(
        {
            "session_id": session_id,
            "source": "text",
            "client_id": client_id,  # 보낸 모니터 자신은 SSE 중복 렌더를 건너뛴다
            "user_text": text,
            "reply_text": reply_text,
            "elapsed_ms": elapsed_ms,
            "stt_ms": 0,
            "first_token_ms": response_start_ms,
            "response_start_ms": response_start_ms,
        }
    )
    return JSONResponse(
        {
            "success": True,
            "session_id": session_id,
            "user_text": text,
            "reply_text": reply_text,
            "elapsed_ms": elapsed_ms,
            "stt_ms": 0,
            "first_token_ms": response_start_ms,
            "response_start_ms": response_start_ms,
        }
    )


@app.post("/api/voice-chat-stream")
async def voice_chat_stream(
    audio: UploadFile = File(...),
    session_id: str = Form("voice"),
) -> Any:
    """음성 파일 → SSE 스트림 (user_text → text_delta* → audio_chunk* → done).

    LLM이 문장을 완성할 때마다 곧바로 TTS를 돌려 mp3 조각을 흘려보내므로,
    전체 응답을 기다리는 /api/voice-chat보다 첫 소리가 훨씬 빨리 나온다.
    """
    blob = await audio.read()
    if len(blob) > MAX_AUDIO_BYTES:
        return _error(413, MSG_AUDIO_TOO_BIG)
    if not blob:
        return _error(400, MSG_NO_SPEECH)

    content_type = (audio.content_type or "audio/webm").split(";")[0].strip()
    extension = _AUDIO_EXTENSIONS.get(content_type, "webm")
    loop = asyncio.get_running_loop()

    def generate() -> Iterator[str]:
        # 동기 제너레이터 → Starlette가 스레드풀에서 돌리므로 이벤트 루프를 막지 않는다.
        started = time.monotonic()

        # a. STT
        try:
            user_text = state.transcribe(
                blob, filename=f"speech.{extension}", content_type=content_type
            )
        except SpeechApiError as exc:
            logger.error("STT 실패: %s", exc)
            yield _sse("error", {"message": MSG_STT_ERROR})
            return
        if not user_text:
            yield _sse("error", {"message": MSG_NO_SPEECH})
            return
        yield _sse("user_text", {"text": user_text})

        # 필러 즉시 재생 — LLM이 생각하는 동안 침묵을 없앤다.
        filler = _pick_filler()
        if filler:
            yield _sse("audio_chunk", {"audio_b64": filler, "audio_mime": "audio/mpeg"})

        # b. LLM 델타 → 문장 단위 TTS 파이프라인.
        #    TTS는 별도 풀에서 미리 돌리되, 재생 순서를 지키려고 완료된 앞 조각부터 내보낸다.
        pending: deque = deque()

        def submit_tts(sentence: str) -> None:
            spoken = _tts_text(sentence).strip()
            if spoken:
                pending.append(_TTS_POOL.submit(_tts_job, spoken))

        stt_done = time.monotonic()
        stt_ms = round((stt_done - started) * 1000)

        parts: list[str] = []
        tail = ""
        first_sent = False
        first_delta_time: Optional[float] = None
        for delta in state.reply_stream(session_id, user_text):
            if first_delta_time is None:
                first_delta_time = time.monotonic()
            parts.append(delta)
            yield _sse("text_delta", {"text": delta})
            sentences, tail = _pop_sentences(tail + delta)
            for sentence in sentences:
                submit_tts(sentence)
                first_sent = True
            # 첫 조각 조기 절단 — 문장이 아직 안 끝났어도 소리부터 시작한다.
            if not first_sent and len(tail) >= _FIRST_CHUNK_CHARS:
                head, tail = _cut_first_chunk(tail)
                if head:
                    submit_tts(head)
                    first_sent = True
            while pending and pending[0].done():
                yield _audio_event(pending.popleft().result())
        if tail.strip():
            submit_tts(tail)
        while pending:
            yield _audio_event(pending.popleft().result())  # 순서 보장 — 앞 조각부터 대기

        reply_text = "".join(parts).strip()
        elapsed_ms = round((time.monotonic() - started) * 1000)
        # 질문이 끝난 시점(녹음 업로드 도착)부터 LLM이 실제 답변을 시작한 시점까지.
        # 필러 음성은 이 구간과 무관하게(STT 직후) 재생되므로 여기엔 안 잡힌다.
        first_token_ms = (
            round((first_delta_time - stt_done) * 1000) if first_delta_time else None
        )
        response_start_ms = (
            round((first_delta_time - started) * 1000) if first_delta_time else None
        )
        yield _sse("done", {"elapsed_ms": elapsed_ms})
        logger.info("voice-chat-stream 완료 session=%s %dms", session_id, elapsed_ms)

        # c. 모니터 중계 — 이 제너레이터는 스레드풀에서 돌므로 루프로 넘겨서 publish
        loop.call_soon_threadsafe(
            broadcaster.publish,
            {
                "session_id": session_id,
                "source": "voice",
                "user_text": user_text,
                "reply_text": reply_text,
                "elapsed_ms": elapsed_ms,
                "stt_ms": stt_ms,
                "first_token_ms": first_token_ms,
                "response_start_ms": response_start_ms,
            },
        )

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/session/end")
def end_session(session_id: str = Form("voice")) -> dict[str, Any]:
    """대화 종료 — 히스토리를 백엔드에 저장하고 맥락을 비운다."""
    return {"success": True, "saved": state.end_session(session_id)}


def _error(status: int, message: str) -> JSONResponse:
    return JSONResponse({"success": False, "message": message}, status_code=status)


_MARKDOWN_CHARS = re.compile(r"[*_`#>]+|\[([^\]]*)\]\([^)]*\)")


def _tts_text(text: str) -> str:
    """웹 챗(초록이) 응답은 마크다운일 수 있어, TTS가 기호를 읽지 않게 벗긴다."""
    return _MARKDOWN_CHARS.sub(lambda m: m.group(1) or "", text)


# ------------------------------------------------- 문장 파이프라이닝 (스트리밍 TTS)

# TTS 동시 2개 — GMS 부하와 순서 지연의 균형점. 문장들이 순차 완성되므로 충분하다.
_TTS_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="tts")

_SENTENCE_END = re.compile(r"[.!?…。]+[\s\"'）)】\]]*|\n+")


def _pop_sentences(buffer: str) -> tuple[list[str], str]:
    """버퍼에서 완성된 문장들을 떼어내고 나머지를 돌려준다."""
    sentences: list[str] = []
    start = 0
    for match in _SENTENCE_END.finditer(buffer):
        sentence = buffer[start : match.end()].strip()
        if sentence:
            sentences.append(sentence)
        start = match.end()
    return sentences, buffer[start:]


# 첫 소리를 빨리 내기 위한 첫 조각 조기 절단 기준.
# 초록이는 구두점 없이 긴 문장을 쓰는 일이 많아, 첫 조각만은 문장 완성을 기다리지
# 않고 이 길이가 모이면 어절 경계에서 잘라 TTS를 시작한다.
_FIRST_CHUNK_CHARS = 20

# 필러(맞장구) — LLM 첫 토큰 지연(GMS 경유 1.4~4.7초로 변동)이 커서, STT 직후
# 미리 합성해둔 짧은 음성을 즉시 재생해 "5초 내 첫 소리"를 항상 보장한다.
_FILLER_TEXTS = ["음, 잠깐만 생각해볼게요!", "어디 보자~", "음~ 좋은 질문이에요!"]
_filler_cache: list[str] = []  # base64 mp3
_filler_ready = threading.Event()


def _warm_fillers() -> None:
    """서버 시작 시 백그라운드로 필러 음성을 미리 합성해둔다."""
    for text in _FILLER_TEXTS:
        try:
            _filler_cache.append(
                base64.b64encode(state.speech.synthesize(text)).decode("ascii")
            )
        except SpeechApiError as exc:
            logger.warning("필러 합성 실패 (없어도 동작): %s", exc)
    _filler_ready.set()
    logger.info("필러 음성 %d개 준비 완료", len(_filler_cache))


def _pick_filler() -> Optional[str]:
    if not _filler_ready.is_set() or not _filler_cache:
        return None
    return random.choice(_filler_cache)


def _warm_local_stt() -> None:
    """로컬 whisper 로드 — 완료 전까지는 GMS 폴백으로 대화가 이미 가능하다."""
    if state.local_stt is None:
        return
    if not state.local_stt.load():
        logger.warning("로컬 STT 사용 불가 — GMS whisper-1로 계속 동작")


threading.Thread(target=_warm_fillers, daemon=True).start()
threading.Thread(target=_warm_local_stt, daemon=True).start()


def _cut_first_chunk(buffer: str) -> tuple[str, str]:
    """쉼표 > 공백 순으로 자연스러운 절단점을 찾아 (첫 조각, 나머지)를 돌려준다."""
    for separator in (", ", ","):
        idx = buffer.rfind(separator, 10)
        if idx > 10:
            cut = idx + len(separator)
            return buffer[:cut].strip(), buffer[cut:]
    idx = buffer.rfind(" ", 10)
    if idx > 10:
        return buffer[:idx].strip(), buffer[idx + 1 :]
    return buffer.strip(), ""


def _tts_job(text: str) -> Optional[str]:
    """TTS 한 조각. 실패는 None — 소리 한 조각이 빠져도 대화는 계속돼야 한다."""
    try:
        return base64.b64encode(state.speech.synthesize(text)).decode("ascii")
    except SpeechApiError as exc:
        logger.error("TTS 조각 실패: %s", exc)
        return None


def _sse(event: str, payload: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _audio_event(audio_b64: Optional[str]) -> str:
    if not audio_b64:
        return ": tts-failed\n\n"  # SSE 주석 — 클라이언트는 그냥 무시한다
    return _sse("audio_chunk", {"audio_b64": audio_b64, "audio_mime": "audio/mpeg"})


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8080)
