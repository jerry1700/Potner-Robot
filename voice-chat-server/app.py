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

import base64
import logging
import sys
import threading
import time
from pathlib import Path
from typing import Any, Optional

import yaml
from fastapi import FastAPI, File, Form, UploadFile
from fastapi.responses import FileResponse, JSONResponse

REPO_ROOT = Path(__file__).resolve().parents[1]
# colcon install 없이도 potner_llm을 import할 수 있게 경로를 올린다 (tests/conftest.py와 동일).
sys.path.insert(0, str(REPO_ROOT / "src" / "potner_llm"))

from potner_llm.client import describe_llm  # noqa: E402
from potner_llm.conversation_backend import create_conversation_backend  # noqa: E402
from potner_llm.dialogue import DialogueService  # noqa: E402
from potner_llm.events import EventStore  # noqa: E402
from potner_llm.status import MetricLevel, PlantStatus  # noqa: E402

from speech import SpeechApiError, SpeechClient  # noqa: E402

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
        with self._lock:
            service = self._sessions.pop(session_id, None)
        if service is None:
            return False
        service.end_conversation()  # 히스토리 저장 후 비움
        return True


logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
state = VoiceChatApp()
app = FastAPI(title="오린카 음성 대화")

logger.info(describe_llm(state.dialogue("voice").llm, state.config))


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "llm": describe_llm(state.dialogue("voice").llm, state.config).splitlines()[0],
    }


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
        user_text = state.speech.transcribe(
            blob, filename=f"speech.{extension}", content_type=content_type
        )
    except SpeechApiError as exc:
        logger.error("STT 실패: %s", exc)
        return _error(502, MSG_STT_ERROR)
    if not user_text:
        return _error(422, MSG_NO_SPEECH)

    # b. LLM (tool use·페르소나·히스토리는 DialogueService가 처리.
    #    LLM 불가 시에도 chat_once가 로컬 템플릿 폴백으로 항상 텍스트를 돌려준다.)
    reply_text = state.dialogue(session_id).chat_once(user_text)

    # c. TTS — 실패해도 텍스트는 돌려준다 (화면 표시는 가능하게)
    audio_b64: Optional[str] = None
    try:
        audio_b64 = base64.b64encode(state.speech.synthesize(reply_text)).decode("ascii")
    except SpeechApiError as exc:
        logger.error("TTS 실패 (텍스트만 반환): %s", exc)

    elapsed_ms = round((time.monotonic() - started) * 1000)
    logger.info("voice-chat 완료 session=%s %dms", session_id, elapsed_ms)

    # d. 응답
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


@app.post("/api/session/end")
def end_session(session_id: str = Form("voice")) -> dict[str, Any]:
    """대화 종료 — 히스토리를 백엔드에 저장하고 맥락을 비운다."""
    return {"success": True, "saved": state.end_session(session_id)}


def _error(status: int, message: str) -> JSONResponse:
    return JSONResponse({"success": False, "message": message}, status_code=status)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8080)
