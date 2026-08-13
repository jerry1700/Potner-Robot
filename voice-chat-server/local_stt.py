"""로컬 STT — faster-whisper (RTX 4070에서 짧은 발화 0.3~0.6초).

GMS whisper-1(왕복 1.3~2.1초)을 대체한다. `SpeechClient.transcribe`와 시그니처를
맞춰 app.py가 백엔드를 갈아끼울 수 있게 한다. 모델 로드(수 초~수십 초)는
`load()`로 분리 — app.py가 시작 시 백그라운드 스레드에서 부르고, 완료 전에는
GMS로 폴백한다.

- 디코딩: faster-whisper가 PyAV를 내장해 webm/mp4/mp3를 그대로 받는다 (ffmpeg 불필요).
- 디바이스: cuda/float16 → 실패 시 cpu/int8 폴백 (Windows에서 ctranslate2 CUDA
  DLL이 안 맞는 경우 대비).
"""

from __future__ import annotations

import io
import logging
import os
import threading
import time

logger = logging.getLogger("voice_chat.local_stt")

DEFAULT_MODEL = "large-v3-turbo"


def _load_cuda_dlls() -> None:
    """pip로 설치한 nvidia-* wheel의 DLL 디렉터리를 ctranslate2가 찾게 등록한다.

    wheel마다 DLL 위치가 다르다(cublas는 bin/, 리눅스 계열은 lib/) —
    nvidia 네임스페이스 아래 bin·lib 디렉터리를 전부 등록한다.
    """
    try:
        import nvidia
    except ImportError:
        return
    for package_dir in nvidia.__path__:
        for name in os.listdir(package_dir):
            for leaf in ("bin", "lib"):
                path = os.path.join(package_dir, name, leaf)
                if os.path.isdir(path):
                    os.add_dll_directory(path)
                    # ctranslate2는 런타임 LoadLibrary로 열어서 PATH도 필요하다.
                    os.environ["PATH"] = path + os.pathsep + os.environ.get("PATH", "")


class LocalSttClient:
    """faster-whisper 래퍼. load() 전에는 ready=False — 호출측이 폴백을 결정한다."""

    def __init__(self, model_name: str | None = None) -> None:
        self.model_name = (
            model_name or os.environ.get("WHISPER_MODEL") or DEFAULT_MODEL
        ).strip()
        self._model = None
        self._lock = threading.Lock()  # 동시 transcribe 직렬화 (GPU 메모리 보호)
        self.device = "?"

    @property
    def ready(self) -> bool:
        return self._model is not None

    def load(self) -> bool:
        """모델 로드 (최초 실행은 HF에서 다운로드). 성공 여부를 돌려준다."""
        import numpy as np
        from faster_whisper import WhisperModel

        _load_cuda_dlls()
        started = time.monotonic()
        for device, compute_type in (("cuda", "float16"), ("cpu", "int8")):
            try:
                model = WhisperModel(
                    self.model_name, device=device, compute_type=compute_type
                )
                # CUDA DLL 오류는 첫 인퍼런스에서야 터진다 — 무음 워밍업으로
                # 실사용을 확정해야 cpu 폴백이 제때 작동한다.
                segments, _ = model.transcribe(
                    np.zeros(8000, dtype=np.float32), language="ko", beam_size=1
                )
                list(segments)
                self.device = f"{device}/{compute_type}"
                self._model = model
                logger.info(
                    f"로컬 whisper 로드 완료: {self.model_name} "
                    f"({self.device}, {time.monotonic() - started:.1f}s)"
                )
                return True
            except Exception as exc:  # noqa: BLE001 — 다음 디바이스로 폴백
                logger.warning(f"whisper {device}/{compute_type} 로드 실패: {exc}")
        return False

    def transcribe(self, audio: bytes, *, filename: str, content_type: str) -> str:
        """음성 → 한국어 텍스트. SpeechClient.transcribe와 동일 시그니처."""
        if self._model is None:
            raise RuntimeError("로컬 whisper가 아직 로드되지 않았습니다")
        started = time.monotonic()
        with self._lock:
            segments, _info = self._model.transcribe(
                io.BytesIO(audio),
                language="ko",
                beam_size=1,
                vad_filter=True,
            )
            text = "".join(segment.text for segment in segments).strip()
        logger.info(
            f"STT(local {self.device}) ok {(time.monotonic() - started) * 1000:.0f}ms "
            f"bytes={len(audio)} text={text[:80]!r}"
        )
        return text
