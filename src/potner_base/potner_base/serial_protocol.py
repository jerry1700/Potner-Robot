"""젯슨과 ESP32 사이의 시리얼 프로토콜.

ROS에 의존하지 않는 순수 파이썬 모듈입니다. CI에서 단위 테스트로 검증됩니다.

사람이 읽을 수 있는 ASCII 한 줄 형식을 씁니다. 바이너리보다 몇 바이트
손해지만 시리얼 모니터를 열어 눈으로 디버깅할 수 있습니다. 초당 20~50줄
수준이라 대역폭은 문제가 되지 않습니다.

    내려보내기   V,<좌 m/s>,<우 m/s>*<체크섬>
                 S*<체크섬>
    올려보내기   O,<좌틱>,<우틱>,<좌범퍼mm>,<우범퍼mm>,<millis>*<체크섬>

체크섬은 '*' 앞까지 모든 문자의 XOR을 2자리 16진수로 적습니다. 모터를
움직이는 명령이라 한 비트만 뒤집혀도 위험해서 넣었습니다.

★ src/potner_firmware/src/main.cpp 와 반드시 일치해야 합니다.
  한쪽만 고치면 통신이 조용히 죽습니다.
"""

from dataclasses import dataclass

# ESP32가 이 시간 동안 명령을 못 받으면 스스로 모터를 멈춥니다. 젯슨이
# 죽거나 USB가 빠져도 로봇이 계속 달리지 않게 하는 최후의 안전장치입니다.
WATCHDOG_TIMEOUT_MS = 500


class ProtocolError(ValueError):
    """수신 문자열이 프로토콜에 맞지 않을 때 발생합니다."""


@dataclass
class BaseFeedback:
    """ESP32가 올려보낸 한 프레임."""

    left_ticks: int
    right_ticks: int
    left_bumper_mm: int
    right_bumper_mm: int
    board_millis: int


def checksum(payload: str) -> int:
    """문자열의 XOR 체크섬."""
    value = 0
    for char in payload:
        value ^= ord(char)
    return value & 0xFF


def encode_velocity(left_mps: float, right_mps: float) -> str:
    """목표 바퀴 속도 명령을 만듭니다."""
    return _frame(f"V,{left_mps:.4f},{right_mps:.4f}")


def encode_stop() -> str:
    """즉시 정지 명령을 만듭니다."""
    return _frame("S")


def decode_feedback(line: str) -> BaseFeedback:
    """ESP32가 보낸 한 줄을 해석합니다.

    Raises:
        ProtocolError: 형식이 어긋나거나 체크섬이 맞지 않을 때
    """
    line = line.strip()
    if not line:
        raise ProtocolError("빈 줄")

    if "*" not in line:
        raise ProtocolError(f"체크섬 구분자(*)가 없음: {line!r}")

    payload, _, checksum_text = line.rpartition("*")

    try:
        received = int(checksum_text, 16)
    except ValueError:
        raise ProtocolError(f"체크섬이 16진수가 아님: {checksum_text!r}")

    expected = checksum(payload)
    if received != expected:
        raise ProtocolError(
            f"체크섬 불일치 (기대 {expected:02X}, 수신 {received:02X}): {line!r}"
        )

    fields = payload.split(",")
    if fields[0] != "O":
        raise ProtocolError(f"알 수 없는 프레임 종류: {fields[0]!r}")
    if len(fields) != 6:
        raise ProtocolError(f"필드 개수가 6이 아님: {len(fields)}개")

    try:
        return BaseFeedback(
            left_ticks=int(fields[1]),
            right_ticks=int(fields[2]),
            left_bumper_mm=int(fields[3]),
            right_bumper_mm=int(fields[4]),
            board_millis=int(fields[5]),
        )
    except ValueError as exc:
        raise ProtocolError(f"숫자 변환 실패: {exc}")


def _frame(payload: str) -> str:
    return f"{payload}*{checksum(payload):02X}\n"
