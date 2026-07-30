"""GMS 게이트웨이 경유 OpenAI 음성 API (STT: whisper-1, TTS: gpt-4o-mini-tts).

반드시 requests를 쓴다 — 같은 요청을 stdlib urllib로 보내면 GMS 프록시가
응답 바디를 통째로 유실한다(200 OK + IncompleteRead, 재시도 무관. 실측).

모델 제약 (2026-07 실측):
- STT는 whisper-1만 허용. gpt-4o-transcribe(-mini)는 GMS allowlist 차단.
- TTS는 gpt-4o-mini-tts만 허용. tts-1(-hd)는 차단.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

import requests

GMS_OPENAI_BASE_URL = "https://gms.ssafy.io/gmsapi/api.openai.com/v1"

STT_MODEL = "whisper-1"
TTS_MODEL = "gpt-4o-mini-tts"
TTS_VOICE = "nova"
# gpt-4o-mini-tts는 instructions로 말투를 조절할 수 있다.
TTS_INSTRUCTIONS = "밝고 다정한 어린 식물 캐릭터의 말투로, 또박또박 자연스럽게 읽어줘."

logger = logging.getLogger(__name__)


class SpeechApiError(RuntimeError):
    """STT/TTS 호출 실패 (재시도 소진)."""


@dataclass
class SpeechClient:
    api_key: str
    base_url: str = GMS_OPENAI_BASE_URL
    timeout_seconds: float = 60.0
    max_retries: int = 2
    retry_backoff_seconds: float = 0.5

    def transcribe(self, audio: bytes, *, filename: str, content_type: str) -> str:
        """음성 → 한국어 텍스트. 실패 시 SpeechApiError."""
        started = time.monotonic()
        resp = self._post_with_retry(
            "/audio/transcriptions",
            data={"model": STT_MODEL, "language": "ko", "response_format": "json"},
            files={"file": (filename, audio, content_type)},
        )
        text = str(resp.json().get("text") or "").strip()
        logger.info(
            "STT ok %.0fms bytes=%d text=%r",
            (time.monotonic() - started) * 1000,
            len(audio),
            text[:80],
        )
        return text

    def synthesize(self, text: str) -> bytes:
        """텍스트 → mp3 바이트. 실패 시 SpeechApiError."""
        started = time.monotonic()
        resp = self._post_with_retry(
            "/audio/speech",
            json={
                "model": TTS_MODEL,
                "voice": TTS_VOICE,
                "input": text,
                "instructions": TTS_INSTRUCTIONS,
                "response_format": "mp3",
            },
        )
        logger.info(
            "TTS ok %.0fms chars=%d mp3=%dB",
            (time.monotonic() - started) * 1000,
            len(text),
            len(resp.content),
        )
        return resp.content

    def _post_with_retry(self, path: str, **kwargs) -> requests.Response:
        url = self.base_url.rstrip("/") + path
        headers = {"Authorization": f"Bearer {self.api_key}"}
        last_error: Exception | None = None

        for attempt in range(self.max_retries + 1):
            try:
                resp = requests.post(
                    url, headers=headers, timeout=self.timeout_seconds, **kwargs
                )
            except requests.RequestException as exc:
                last_error = exc
                logger.warning("음성 API 연결 오류 (attempt %d): %s", attempt + 1, exc)
            else:
                if resp.status_code == 200:
                    return resp
                last_error = SpeechApiError(
                    f"HTTP {resp.status_code}: {resp.text[:200]}"
                )
                # 429/5xx만 재시도 가치가 있다. 4xx는 바로 포기.
                if resp.status_code not in {429, 500, 502, 503, 504}:
                    break
                logger.warning(
                    "음성 API HTTP %d (attempt %d): %s",
                    resp.status_code,
                    attempt + 1,
                    resp.text[:200],
                )
            if attempt < self.max_retries:
                time.sleep(self.retry_backoff_seconds * (2**attempt))

        raise SpeechApiError(f"음성 API 실패 ({url}): {last_error}") from last_error
