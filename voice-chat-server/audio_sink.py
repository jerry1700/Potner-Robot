"""합성한 오디오를 어디로 내보낼지 — 브라우저냐 로봇 스피커냐.

폰 브라우저로 대화를 시험할 때는 조각을 SSE 로 흘려보내면 폰이 재생합니다.
하지만 이 서버가 로봇에서 돌 때는 소리가 **로봇 스피커**에서 나야 합니다.

로봇 스피커는 speaker_node 가 단독으로 소유합니다. 여기서 aplay 를 직접
띄우면 mission_manager 의 귀가 인사말과 오디오 디바이스를 다투게 됩니다.
그래서 조각을 스풀 디렉터리에 쓰고 **경로만** ROS 토픽으로 넘깁니다.
순서·중복·선점 판단은 전부 speaker_node 쪽 PlaybackQueue 가 합니다.

    AUDIO_SINK=browser   폰이 재생 (기본. 개발 PC 에서 쓰는 값)
    AUDIO_SINK=speaker   로봇 스피커가 재생
    AUDIO_SINK=both      둘 다 (모니터로 확인하면서 로봇으로 들을 때)

rclpy 는 **지연 import** 합니다. 이 모듈은 ROS 없는 Windows 개발 환경과
pytest 에서도 import 되어야 합니다 — speaker 모드를 실제로 켤 때만 필요합니다.
"""

from __future__ import annotations

import json
import logging
import os
import struct
import threading
from pathlib import Path
from typing import Optional, Protocol

logger = logging.getLogger(__name__)

# speaker_node 가 구독하는 토픽. 바꾸려면 노드 쪽도 함께 고쳐야 합니다.
PLAY_AUDIO_TOPIC = "tts/play_audio"

# 재생 중단. 사용자가 대화 페이지를 떠나면 남은 답변을 그치게 합니다.
CANCEL_TOPIC = "tts/cancel"

# speaker_node 의 audio_spool_dir 파라미터와 같은 곳을 가리켜야 합니다.
DEFAULT_SPOOL_DIR = "/tmp/potner-tts"

# 스풀에 남겨두는 조각 수. 재생이 끝난 파일은 지워도 되지만, 방금 지운
# 파일을 재생하려는 경합을 피하려고 여유를 둡니다. 조각 하나가 수십 KB 라
# 이 정도는 디스크에 부담이 없습니다.
SPOOL_KEEP = 40


class AudioSink(Protocol):
    """합성된 조각 한 개를 내보내는 곳."""

    def emit(self, turn_id: str, seq: int, audio: bytes, final: bool = False) -> None:
        ...

    def cancel(self, reason: str = "") -> None:
        """재생 중인 것과 대기 중인 것을 모두 그치게 합니다."""
        ...

    def close(self) -> None:
        ...

    @property
    def plays_on_browser(self) -> bool:
        """True 면 app.py 가 SSE 로도 조각을 내보냅니다."""
        ...

    @property
    def audio_format(self) -> str:
        """이 싱크가 필요한 TTS response_format.

        싱크가 재생 수단을 아니까 포맷도 싱크가 정합니다. 로봇의 aplay 는
        mp3 를 못 읽으므로 스피커 싱크는 wav 를 요구합니다.
        """
        ...


def patch_wav_sizes(audio: bytes) -> bytes:
    """스트리밍 wav 헤더의 크기 필드를 실제 바이트 수로 채웁니다.

    GMS TTS 가 주는 wav 는 길이를 미리 모르는 스트리밍 형태라 RIFF 와 data
    청크의 크기가 둘 다 0xFFFFFFFF 로 옵니다 (2026-08-03 실측). 파일로
    떨어뜨릴 때는 실제 크기를 아니까 채워 넣습니다. aplay 는 EOF 까지 읽어서
    이대로도 재생되지만, 크기가 거짓인 파일은 다른 도구에서 재생 시간을
    잘못 계산하고 진단할 때 사람을 헷갈리게 합니다.

    wav 가 아니거나 형태가 예상과 다르면 원본을 그대로 돌려줍니다.
    """
    if len(audio) < 44 or audio[:4] != b"RIFF" or audio[8:12] != b"WAVE":
        return audio

    patched = bytearray(audio)
    struct.pack_into("<I", patched, 4, len(audio) - 8)

    # 청크를 훑어 data 를 찾습니다. fmt 뒤에 LIST 같은 것이 끼어 있을 수 있습니다.
    offset = 12
    while offset + 8 <= len(audio):
        chunk_id = bytes(audio[offset : offset + 4])
        (declared,) = struct.unpack_from("<I", audio, offset + 4)
        body = offset + 8

        if chunk_id == b"data":
            struct.pack_into("<I", patched, offset + 4, len(audio) - body)
            return bytes(patched)

        if declared in (0xFFFFFFFF, 0) or body + declared > len(audio):
            # 크기를 믿을 수 없으니 더 훑어도 의미가 없습니다.
            return bytes(patched)
        offset = body + declared + (declared & 1)

    return bytes(patched)


class SpoolWriter:
    """조각을 파일로 떨어뜨리고 봉투를 만듭니다.

    ROS 에 의존하지 않습니다 — 발행만 RosSpeakerSink 가 합니다. 파일 쓰기와
    정리는 실기 없이도 검증할 수 있어야 하고, rclpy 가 있어야만 도는 코드에
    섞어두면 pytest 에서 손댈 수 없습니다.
    """

    def __init__(
        self,
        spool_dir: str | Path = DEFAULT_SPOOL_DIR,
        *,
        extension: str = "wav",
        keep: int = SPOOL_KEEP,
    ) -> None:
        self.spool_dir = Path(spool_dir)
        self.extension = extension.lstrip(".")
        self.keep = keep
        self.spool_dir.mkdir(parents=True, exist_ok=True)

    def write(self, turn_id: str, seq: int, audio: bytes) -> Path:
        """조각을 쓰고 경로를 돌려줍니다."""
        if self.extension == "wav":
            audio = patch_wav_sizes(audio)

        path = self.spool_dir / f"turn_{_safe(turn_id)}_{seq:04d}.{self.extension}"
        # 같은 디렉터리에 임시로 쓰고 rename — speaker_node 가 반쯤 쓰인
        # 파일을 재생하는 것을 막습니다(rename 은 같은 파일시스템에서 원자적).
        temporary = path.with_suffix(path.suffix + ".part")
        temporary.write_bytes(audio)
        temporary.replace(path)
        return path

    def prune(self) -> int:
        """오래된 조각을 지웁니다. 서버를 오래 켜두면 스풀이 계속 자랍니다."""
        try:
            files = sorted(
                self.spool_dir.glob(f"turn_*.{self.extension}"),
                key=lambda p: (p.stat().st_mtime, p.name),
            )
        except OSError as exc:
            logger.debug(f"스풀 정리 건너뜀: {exc}")
            return 0

        removed = 0
        for stale in files[: max(0, len(files) - self.keep)]:
            try:
                stale.unlink()
                removed += 1
            except OSError:
                pass  # 재생 중이면 다음 기회에 지워집니다
        return removed


class NullSink:
    """아무 데도 안 보냅니다 — 브라우저만 재생하는 기본 모드."""

    def emit(self, turn_id: str, seq: int, audio: bytes, final: bool = False) -> None:
        return None

    def cancel(self, reason: str = "") -> None:
        return None

    def close(self) -> None:
        return None

    @property
    def plays_on_browser(self) -> bool:
        return True

    @property
    def audio_format(self) -> str:
        # 브라우저만 재생하면 mp3 가 유리합니다 — wav 의 1/3 크기라 Wi-Fi
        # 로 base64 로 실어 보낼 때 눈에 띄게 가볍습니다.
        return "mp3"

    def __repr__(self) -> str:
        return "NullSink(browser)"


class RosSpeakerSink:
    """조각을 스풀에 쓰고 speaker_node 에 경로를 발행합니다."""

    def __init__(
        self,
        *,
        spool_dir: str = DEFAULT_SPOOL_DIR,
        extension: str = "wav",
        also_browser: bool = False,
        keep: int = SPOOL_KEEP,
        topic: str = PLAY_AUDIO_TOPIC,
        cancel_topic: str = CANCEL_TOPIC,
    ) -> None:
        self.spool = SpoolWriter(spool_dir, extension=extension, keep=keep)
        self.also_browser = also_browser
        self.topic = topic
        self.cancel_topic = cancel_topic

        # rclpy 는 여기서만 필요합니다. 없으면 ImportError 가 그대로 올라가서
        # build_audio_sink 가 브라우저 모드로 물러납니다.
        import rclpy
        from rclpy.node import Node
        from std_msgs.msg import String

        self._String = String
        self._owns_context = not rclpy.ok()
        if self._owns_context:
            rclpy.init(args=None)
        self._rclpy = rclpy

        self._node = Node("voice_chat_audio_sink")
        self._publisher = self._node.create_publisher(String, self.topic, 10)
        self._canceller = self._node.create_publisher(String, self.cancel_topic, 10)
        self._lock = threading.Lock()

        logger.info(
            f"오디오 싱크: 로봇 스피커 (topic={self.topic} spool={self.spool.spool_dir} "
            f"ext={self.spool.extension} browser={self.also_browser})"
        )

    @property
    def plays_on_browser(self) -> bool:
        return self.also_browser

    @property
    def audio_format(self) -> str:
        return self.spool.extension

    def emit(self, turn_id: str, seq: int, audio: bytes, final: bool = False) -> None:
        """조각을 파일로 쓰고 경로를 발행합니다.

        발행 실패는 예외로 올립니다 — 부르는 쪽(app.py)이 로그만 남기고
        대화를 계속하도록 감쌉니다. 조각 하나가 못 나갔다고 턴 전체를
        죽이면 사용자는 아무 답도 못 받습니다.
        """
        path = self.spool.write(turn_id, seq, audio)

        message = self._String()
        message.data = _envelope(turn_id, seq, str(path), final)
        with self._lock:
            self._publisher.publish(message)

        logger.debug(f"조각 발행 {turn_id}#{seq} -> {path} ({len(audio)}B)")
        self.spool.prune()

    def cancel(self, reason: str = "") -> None:
        """speaker_node 에 즉시 그치라고 알립니다."""
        message = self._String()
        message.data = reason
        with self._lock:
            self._canceller.publish(message)
        logger.info(f"재생 중단 요청 ({reason or '이유 없음'})")

    def close(self) -> None:
        try:
            self._node.destroy_node()
        except Exception as exc:  # 종료 경로에서 예외를 삼킵니다
            logger.warning(f"싱크 노드 정리 실패: {exc}")
        if self._owns_context and self._rclpy.ok():
            self._rclpy.shutdown()

    def __repr__(self) -> str:
        return (
            f"RosSpeakerSink(topic={self.topic!r}, "
            f"spool={str(self.spool.spool_dir)!r})"
        )


def _safe(turn_id: str) -> str:
    """턴 아이디를 파일명에 쓸 수 있게 다듬습니다."""
    cleaned = "".join(char if char.isalnum() or char in "-_" else "_" for char in turn_id)
    return cleaned[:64] or "turn"


def _envelope(turn_id: str, seq: int, path: str, final: bool) -> str:
    """tts/play_audio 봉투. audio_queue.parse_play_request 와 짝입니다."""
    return json.dumps(
        {"turnId": turn_id, "seq": seq, "path": path, "final": final},
        ensure_ascii=False,
        separators=(",", ":"),
    )


MODES = ("browser", "speaker", "both")


def resolve_mode(mode: Optional[str] = None) -> str:
    """AUDIO_SINK 값을 정규화합니다. 모르는 값은 browser 로 물러납니다."""
    resolved = (mode or os.environ.get("AUDIO_SINK") or "browser").strip().lower()
    if resolved not in MODES:
        logger.warning(
            f"AUDIO_SINK={resolved!r} 를 모르겠습니다 "
            f"({'|'.join(MODES)} 중 하나) — browser 로 갑니다"
        )
        return "browser"
    return resolved


def build_audio_sink(
    *, mode: Optional[str] = None, spool_dir: Optional[str] = None
) -> AudioSink:
    """AUDIO_SINK 환경변수를 보고 싱크를 만듭니다.

    speaker 모드를 켰는데 rclpy 가 없거나 노드 생성이 실패하면 브라우저
    모드로 물러납니다. 로봇에서 ROS 를 source 하지 않고 서버를 띄웠을 때
    대화가 아예 안 되는 것보다, 폰으로라도 들리는 편이 낫습니다.
    """
    resolved = resolve_mode(mode)

    if resolved == "browser":
        logger.info("오디오 싱크: 브라우저 (폰이 재생)")
        return NullSink()

    try:
        return RosSpeakerSink(
            spool_dir=spool_dir or os.environ.get("TTS_SPOOL_DIR", DEFAULT_SPOOL_DIR),
            # 로봇은 aplay 로 재생하므로 wav 여야 합니다. mp3 로 되돌리려면
            # speaker_node 의 audio_player 를 mpg123 으로 함께 바꾸세요.
            extension="wav",
            also_browser=(resolved == "both"),
        )
    except Exception as exc:
        logger.error(
            f"로봇 스피커 싱크를 만들지 못했습니다 ({exc}) — 브라우저 재생으로 "
            "계속합니다. 로봇에서라면 `source install/setup.bash` 를 했는지 확인하세요."
        )
        return NullSink()
