"""GMS 게이트웨이 경유 OpenAI 음성 API (STT: whisper-1, TTS: gpt-4o-mini-tts).

반드시 requests를 쓴다 — 같은 요청을 stdlib urllib로 보내면 GMS 프록시가
응답 바디를 통째로 유실한다(200 OK + IncompleteRead, 재시도 무관. 실측).

모델 제약 (2026-07 실측):
- STT는 whisper-1만 허용. gpt-4o-transcribe(-mini)는 GMS allowlist 차단.
- TTS는 gpt-4o-mini-tts만 허용. tts-1(-hd)는 차단.
- allowlist는 모델 단위라 response_format은 자유롭다. mp3/wav 둘 다 200
  (2026-08-03 실측). 로봇 스피커로 내보낼 때는 wav 를 쓴다 — aplay 로 바로
  재생되어 mpg123 을 깔지 않아도 된다.
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

# 브라우저 재생용 기본값. 로봇 스피커로 보낼 때는 app.py 가 wav 로 바꾼다.
TTS_FORMAT = "mp3"

# response_format -> (MIME, 파일 확장자). 스풀 파일명과 SSE 의 audio_mime
# 이 갈라지지 않게 한곳에서 관리한다.
AUDIO_FORMATS = {
    "mp3": ("audio/mpeg", "mp3"),
    "wav": ("audio/wav", "wav"),
    "opus": ("audio/ogg", "ogg"),
    "flac": ("audio/flac", "flac"),
}
# gpt-4o-mini-tts는 instructions로 말투를 조절할 수 있다.
TTS_INSTRUCTIONS = "밝고 다정한 어린 식물 캐릭터의 말투로, 또박또박 자연스럽게 읽어줘."

logger = logging.getLogger(__name__)


class SpeechApiError(RuntimeError):
    """STT/TTS 호출 실패 (재시도 소진)."""


def audio_mime(fmt: str) -> str:
    """response_format 에 맞는 MIME. 모르는 포맷은 옥텟 스트림."""
    return AUDIO_FORMATS.get(fmt, ("application/octet-stream", fmt))[0]


def audio_extension(fmt: str) -> str:
    """response_format 에 맞는 파일 확장자."""
    return AUDIO_FORMATS.get(fmt, ("", fmt))[1]


@dataclass
class SpeechClient:
    api_key: str
    base_url: str = GMS_OPENAI_BASE_URL
    timeout_seconds: float = 60.0
    max_retries: int = 2
    retry_backoff_seconds: float = 0.5
    audio_format: str = TTS_FORMAT
    """합성 결과 포맷. 로봇 스피커(aplay)로 보낼 때는 "wav"."""

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
            f"STT ok {(time.monotonic() - started) * 1000:.0f}ms "
            f"bytes={len(audio)} text={text[:80]!r}"
        )
        return text

    def synthesize(self, text: str, *, fmt: str | None = None) -> bytes:
        """텍스트 → 오디오 바이트. 실패 시 SpeechApiError.

        Args:
            text: 읽을 문장
            fmt: response_format. 생략하면 audio_format(기본 mp3).
        """
        started = time.monotonic()
        resolved = fmt or self.audio_format
        resp = self._post_with_retry(
            "/audio/speech",
            json={
                "model": TTS_MODEL,
                "voice": TTS_VOICE,
                "input": text,
                "instructions": TTS_INSTRUCTIONS,
                "response_format": resolved,
            },
        )
        logger.info(
            f"TTS ok {(time.monotonic() - started) * 1000:.0f}ms "
            f"chars={len(text)} {resolved}={len(resp.content)}B"
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
                logger.warning(f"음성 API 연결 오류 (attempt {attempt + 1}): {exc}")
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
                    f"음성 API HTTP {resp.status_code} "
                    f"(attempt {attempt + 1}): {resp.text[:200]}"
                )
            if attempt < self.max_retries:
                time.sleep(self.retry_backoff_seconds * (2**attempt))

        raise SpeechApiError(f"음성 API 실패 ({url}): {last_error}") from last_error
