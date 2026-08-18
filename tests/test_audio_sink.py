"""오디오 싱크 검증 — 합성한 조각이 로봇 스피커까지 가는 길.

rclpy 없이 돕니다. RosSpeakerSink 의 ROS 부분(발행)은 CI 에서 테스트하지
않고, 파일 쓰기·정리·봉투 조립은 SpoolWriter 로 떼어내 검증합니다.

가장 중요한 것은 마지막의 **경계 검증**입니다. 봉투를 만드는 쪽
(voice-chat-server/audio_sink.py)과 읽는 쪽(potner_base/audio_queue.py)이
다른 트리에 있어서, 한쪽만 고치면 조용히 어긋납니다.
"""

import struct
import sys
from pathlib import Path

import pytest

# voice-chat-server 는 colcon 패키지가 아니라 conftest 가 올려주지 않습니다.
SERVER_DIR = Path(__file__).resolve().parent.parent / "voice-chat-server"
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

from audio_sink import (  # noqa: E402
    CANCEL_TOPIC,
    PLAY_AUDIO_TOPIC,
    NullSink,
    SpoolWriter,
    _envelope,
    _safe,
    build_audio_sink,
    patch_wav_sizes,
    resolve_mode,
)

from potner_base.audio_queue import parse_play_request  # noqa: E402


def wav_bytes(samples: bytes = b"\x00\x01" * 100, *, streaming: bool = True) -> bytes:
    """GMS TTS 가 주는 것과 같은 모양의 wav 를 만듭니다."""
    size = 0xFFFFFFFF if streaming else len(samples)
    riff = 0xFFFFFFFF if streaming else 36 + len(samples)
    header = b"RIFF" + struct.pack("<I", riff) + b"WAVE"
    header += b"fmt " + struct.pack("<IHHIIHH", 16, 1, 1, 24000, 48000, 2, 16)
    header += b"data" + struct.pack("<I", size)
    return header + samples


# --- wav 크기 필드 패치 ---


def test_스트리밍_wav의_크기_필드를_실제_값으로_채운다():
    """GMS TTS 는 길이를 미리 몰라 RIFF/data 크기를 0xFFFFFFFF 로 보냅니다."""
    samples = b"\x00\x01" * 100
    raw = wav_bytes(samples)
    assert struct.unpack_from("<I", raw, 4)[0] == 0xFFFFFFFF

    patched = patch_wav_sizes(raw)

    assert len(patched) == len(raw)
    assert struct.unpack_from("<I", patched, 4)[0] == len(raw) - 8
    assert struct.unpack_from("<I", patched, 40)[0] == len(samples)


def test_이미_올바른_wav는_그대로_둔다():
    raw = wav_bytes(streaming=False)
    assert patch_wav_sizes(raw) == raw


@pytest.mark.parametrize(
    "blob",
    [
        b"",
        b"\xff\xf3\xc4\xc4" + b"\x00" * 100,  # mp3
        b"RIFF" + b"\x00" * 4 + b"AVI " + b"\x00" * 40,  # wav 아님
        b"RIFF" + b"\x00" * 8,  # 너무 짧음
    ],
)
def test_wav가_아니면_건드리지_않는다(blob):
    """mp3 로 되돌려도 이 함수가 바이트를 망가뜨리면 안 됩니다."""
    assert patch_wav_sizes(blob) == blob


def test_data_청크가_없어도_죽지_않는다():
    header = b"RIFF" + struct.pack("<I", 0xFFFFFFFF) + b"WAVE"
    header += b"fmt " + struct.pack("<IHHIIHH", 16, 1, 1, 24000, 48000, 2, 16)
    header += b"LIST" + struct.pack("<I", 4) + b"INFO"
    result = patch_wav_sizes(header + b"\x00" * 8)
    assert len(result) == len(header) + 8


# --- 스풀 쓰기 ---


def test_조각을_스풀에_쓴다(tmp_path):
    spool = SpoolWriter(tmp_path, extension="wav")
    path = spool.write("abc", 0, wav_bytes())

    assert path.is_file()
    assert path.parent == tmp_path
    assert path.name == "turn_abc_0000.wav"


def test_스풀에_임시파일이_남지_않는다(tmp_path):
    """반쯤 쓰인 파일을 재생하지 않게 rename 으로 마무리합니다."""
    spool = SpoolWriter(tmp_path, extension="wav")
    spool.write("abc", 0, wav_bytes())

    assert list(tmp_path.glob("*.part")) == []


def test_seq가_파일명에서_정렬된다(tmp_path):
    """번호가 문자열로 정렬돼도 순서가 유지되게 0 을 채웁니다."""
    spool = SpoolWriter(tmp_path, extension="wav")
    for seq in (0, 2, 10):
        spool.write("abc", seq, wav_bytes())

    names = sorted(p.name for p in tmp_path.glob("*.wav"))
    assert names == ["turn_abc_0000.wav", "turn_abc_0002.wav", "turn_abc_0010.wav"]


def test_스풀_디렉터리를_만든다(tmp_path):
    target = tmp_path / "deep" / "spool"
    SpoolWriter(target, extension="wav")
    assert target.is_dir()


def test_wav로_쓰면_크기_필드가_패치된다(tmp_path):
    spool = SpoolWriter(tmp_path, extension="wav")
    written = spool.write("abc", 0, wav_bytes()).read_bytes()

    assert struct.unpack_from("<I", written, 4)[0] != 0xFFFFFFFF


def test_mp3로_쓰면_바이트를_건드리지_않는다(tmp_path):
    raw = b"\xff\xf3\xc4\xc4" + b"\x00" * 50
    spool = SpoolWriter(tmp_path, extension="mp3")
    path = spool.write("abc", 0, raw)

    assert path.suffix == ".mp3"
    assert path.read_bytes() == raw


# --- 스풀 정리 ---


def test_오래된_조각을_지운다(tmp_path):
    """서버를 오래 켜두면 스풀이 계속 자랍니다."""
    spool = SpoolWriter(tmp_path, extension="wav", keep=3)
    for seq in range(10):
        spool.write("abc", seq, wav_bytes())
        spool.prune()

    remaining = sorted(p.name for p in tmp_path.glob("*.wav"))
    assert len(remaining) == 3
    # 최신 것이 남아야 합니다 — 방금 만든 조각을 지우면 재생이 실패합니다.
    assert "turn_abc_0009.wav" in remaining


def test_상한_이하면_아무것도_지우지_않는다(tmp_path):
    spool = SpoolWriter(tmp_path, extension="wav", keep=5)
    for seq in range(3):
        spool.write("abc", seq, wav_bytes())

    assert spool.prune() == 0
    assert len(list(tmp_path.glob("*.wav"))) == 3


def test_다른_확장자_파일은_정리에서_건드리지_않는다(tmp_path):
    (tmp_path / "keep-me.mp3").write_bytes(b"x")
    spool = SpoolWriter(tmp_path, extension="wav", keep=0)
    spool.write("abc", 0, wav_bytes())
    spool.prune()

    assert (tmp_path / "keep-me.mp3").is_file()


# --- 파일명 안전 ---


@pytest.mark.parametrize(
    "turn_id",
    ["../../etc/passwd", "a/b/c", "..", "turn;rm -rf /", "턴한글"],
)
def test_턴아이디가_경로를_벗어나지_못한다(turn_id, tmp_path):
    """턴 아이디는 서버가 만들지만, 파일명에 그대로 쓰면 위험합니다."""
    spool = SpoolWriter(tmp_path, extension="wav")
    path = spool.write(turn_id, 0, wav_bytes())

    assert path.parent == tmp_path
    assert "/" not in _safe(turn_id)
    assert ".." not in _safe(turn_id)


def test_빈_턴아이디도_파일명이_된다():
    assert _safe("") == "turn"
    assert _safe("///") == "___"


def test_아주_긴_턴아이디를_자른다():
    assert len(_safe("x" * 500)) <= 64


# --- 모드 선택 ---


@pytest.mark.parametrize("value", ["browser", "BROWSER", " browser "])
def test_브라우저_모드를_읽는다(value):
    assert resolve_mode(value) == "browser"


@pytest.mark.parametrize("value", ["speaker", "both"])
def test_스피커_모드를_읽는다(value):
    assert resolve_mode(value) == value


@pytest.mark.parametrize("value", ["", "wat", "robot", None])
def test_모르는_모드는_브라우저로_물러난다(value, monkeypatch):
    monkeypatch.delenv("AUDIO_SINK", raising=False)
    assert resolve_mode(value) == "browser"


def test_환경변수를_읽는다(monkeypatch):
    monkeypatch.setenv("AUDIO_SINK", "both")
    assert resolve_mode() == "both"


def test_브라우저_모드는_NullSink다(monkeypatch):
    monkeypatch.delenv("AUDIO_SINK", raising=False)
    sink = build_audio_sink()

    assert isinstance(sink, NullSink)
    assert sink.plays_on_browser is True
    assert sink.audio_format == "mp3"


def test_rclpy가_없으면_브라우저로_물러난다():
    """로봇에서 source install/setup.bash 를 빼먹어도 폰으로는 들려야 합니다."""
    try:
        import rclpy  # noqa: F401
    except ImportError:
        assert isinstance(build_audio_sink(mode="speaker"), NullSink)
    else:
        pytest.skip("rclpy 가 있는 환경에서는 실제 노드를 만듭니다")


def test_NullSink는_아무것도_하지_않는다():
    """브라우저 모드에서 emit/cancel/close 가 조용히 통과해야 합니다."""
    sink = NullSink()
    assert sink.emit("t", 0, b"x", final=True) is None
    assert sink.cancel("이유") is None
    assert sink.close() is None


# --- 봉투 경계 (가장 중요) ---


def test_서버가_만든_봉투를_노드가_읽는다():
    """봉투를 만드는 쪽과 읽는 쪽이 다른 트리에 있어 조용히 어긋납니다.

    audio_sink._envelope (voice-chat-server) 가 만든 것을
    audio_queue.parse_play_request (potner_base) 가 그대로 읽어야 합니다.
    """
    payload = _envelope("abc123", 7, "/tmp/potner-tts/turn_abc123_0007.wav", True)
    request = parse_play_request(payload)

    assert request.turn_id == "abc123"
    assert request.seq == 7
    assert request.path == "/tmp/potner-tts/turn_abc123_0007.wav"
    assert request.final is True


def test_한글이_섞인_봉투도_왕복한다():
    request = parse_play_request(_envelope("턴-1", 0, "/tmp/한글.wav", False))
    assert request.turn_id == "턴-1"
    assert request.path == "/tmp/한글.wav"


def test_윈도우_경로가_들어간_봉투도_왕복한다():
    r"""개발 PC 에서는 C:\... 경로가 실립니다 — 역슬래시가 깨지면 안 됩니다."""
    win = r"C:\Temp\potner-tts\turn_abc_0000.wav"
    assert parse_play_request(_envelope("abc", 0, win, False)).path == win


def test_스풀에_쓴_실제_경로가_봉투로_왕복한다(tmp_path):
    """파일명 규칙과 봉투가 함께 어긋나는 것을 막습니다."""
    spool = SpoolWriter(tmp_path, extension="wav")
    path = spool.write("abc", 3, wav_bytes())

    request = parse_play_request(_envelope("abc", 3, str(path), False))

    assert Path(request.path) == path
    assert Path(request.path).is_file()


def test_토픽_이름이_노드와_같다():
    """노드 쪽 구독 토픽과 문자열이 같아야 합니다 — 다르면 조용히 안 들립니다."""
    node_source = (
        Path(__file__).resolve().parent.parent
        / "src"
        / "potner_base"
        / "potner_base"
        / "speaker_node.py"
    ).read_text(encoding="utf-8")

    assert f'"{PLAY_AUDIO_TOPIC}"' in node_source
    assert f'"{CANCEL_TOPIC}"' in node_source
