"""젯슨 <-> ESP32 시리얼 프로토콜 검증.

모터를 움직이는 명령이라 한 바이트만 깨져도 위험합니다. 체크섬이
제대로 걸러내는지 확인합니다.
"""

import pytest

from potner_base import serial_protocol as proto


def test_속도_명령은_줄바꿈으로_끝난다():
    frame = proto.encode_velocity(0.15, -0.15)
    assert frame.startswith("V,0.1500,-0.1500*")
    assert frame.endswith("\n")


def test_정지_명령():
    assert proto.encode_stop().startswith("S*")


def test_정상_프레임을_해석한다():
    payload = "O,1440,-720,350,1200,98765"
    line = f"{payload}*{proto.checksum(payload):02X}"

    feedback = proto.decode_feedback(line)
    assert feedback.left_ticks == 1440
    assert feedback.right_ticks == -720
    assert feedback.left_bumper_mm == 350
    assert feedback.right_bumper_mm == 1200
    assert feedback.board_millis == 98765


def test_체크섬이_틀리면_거부한다():
    """노이즈로 한 글자가 바뀐 상황. 이걸 통과시키면 오도메트리가 튑니다."""
    payload = "O,1440,-720,350,1200,98765"
    good = proto.checksum(payload)
    line = f"{payload}*{good ^ 0xFF:02X}"

    with pytest.raises(proto.ProtocolError):
        proto.decode_feedback(line)


def test_체크섬이_없으면_거부한다():
    with pytest.raises(proto.ProtocolError):
        proto.decode_feedback("O,1440,-720,350,1200,98765")


def test_필드가_모자라면_거부한다():
    payload = "O,1440,-720"
    line = f"{payload}*{proto.checksum(payload):02X}"
    with pytest.raises(proto.ProtocolError):
        proto.decode_feedback(line)


def test_빈_줄이나_쓰레기를_거부한다():
    for garbage in ("", "   ", "\x00\xff*FF"):
        with pytest.raises(proto.ProtocolError):
            proto.decode_feedback(garbage)


def test_인코딩과_디코딩이_같은_체크섬을_쓴다():
    """양쪽 구현이 어긋나면 통신이 조용히 죽습니다."""
    payload = "O,1,2,3,4,5"
    line = proto._frame(payload).strip()
    assert proto.decode_feedback(line).left_ticks == 1
