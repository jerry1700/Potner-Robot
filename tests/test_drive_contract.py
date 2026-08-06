"""서버와 Jetson 사이 수동 주행 명령 MQTT 계약 검증."""

import json
import math

import pytest

from potner_bridge.command_result import CommandError
from potner_bridge.drive_contract import (
    DRIVE,
    DriveCommand,
    drive_command_json,
    hold_seconds,
    parse_drive_command,
    parse_internal_drive_command,
)

TURN_22_5 = math.radians(22.5)

REQUEST_ID = "3ff0d4af-9217-4473-b7a4-f8ff8d420daa"


def payload(**overrides):
    body = {
        "direction": "FORWARD",
        "linearMps": 0.12,
        "angularRps": 0.0,
        "durationMs": 600,
        "requestId": REQUEST_ID,
    }
    body.update(overrides)
    for key in [name for name, value in body.items() if value is _MISSING]:
        del body[key]
    return json.dumps(body)


class _Missing:
    pass


_MISSING = _Missing()


def test_주행_명령을_속도와_지속시간으로_해석한다():
    command = parse_drive_command(payload().encode())

    assert command.direction == "FORWARD"
    assert command.linear_mps == 0.12
    assert command.angular_rps == 0.0
    assert command.duration_ms == 600
    assert command.request_id == REQUEST_ID
    assert command.command_name == DRIVE


@pytest.mark.parametrize(
    "direction,linear,angular",
    [
        ("FORWARD", 0.12, 0.0),
        ("BACKWARD", -0.12, 0.0),
        ("LEFT", 0.0, 0.6),
        ("RIGHT", 0.0, -0.6),
        ("STOP", 0.0, 0.0),
    ],
)
def test_서버가_보내는_다섯_방향을_모두_받는다(direction, linear, angular):
    command = parse_drive_command(
        payload(direction=direction, linearMps=linear, angularRps=angular)
    )

    assert command.direction == direction
    assert command.linear_mps == linear
    assert command.angular_rps == angular


def test_direction은_로그용이라_모르는_값도_받는다():
    """실제로 로봇을 움직이는 값은 linearMps/angularRps 뿐이다.

    여기서 direction 값을 다섯 개로 제한하면, 서버가 새 라벨을 추가했을
    때 정상적인 속도 명령까지 거절하게 된다.
    """
    command = parse_drive_command(payload(direction="DIAGONAL"))

    assert command.direction == "DIAGONAL"


def test_STOP은_지속시간_0을_받는다():
    command = parse_drive_command(
        payload(direction="STOP", linearMps=0.0, angularRps=0.0, durationMs=0)
    )

    assert command.duration_ms == 0


@pytest.mark.parametrize(
    "body,code",
    [
        ("not-json", "INVALID_JSON"),
        ("[]", "INVALID_PAYLOAD"),
        (payload(requestId=_MISSING), "INVALID_IDENTIFIER"),
        (payload(requestId=""), "INVALID_IDENTIFIER"),
        (payload(requestId="x" * 101), "INVALID_IDENTIFIER"),
        (payload(linearMps=_MISSING), "INVALID_VELOCITY"),
        (payload(linearMps="0.12"), "INVALID_VELOCITY"),
        (payload(angularRps=_MISSING), "INVALID_VELOCITY"),
        (payload(durationMs=_MISSING), "INVALID_DURATION"),
        (payload(durationMs=-1), "INVALID_DURATION"),
        (payload(durationMs="600"), "INVALID_DURATION"),
        (payload(direction=_MISSING), "INVALID_DIRECTION"),
        (payload(direction=""), "INVALID_DIRECTION"),
    ],
)
def test_형식이_잘못된_명령은_거부한다(body, code):
    with pytest.raises(CommandError) as error:
        parse_drive_command(body)

    assert error.value.code == code


def test_거절할_때도_requestId를_건져_로그에_남길_수_있다():
    with pytest.raises(CommandError) as error:
        parse_drive_command(payload(linearMps="0.12"))

    assert error.value.request_id == REQUEST_ID


def test_requestId가_UUID가_아니어도_받는다():
    command = parse_drive_command(payload(requestId="cmd-42"))

    assert command.request_id == "cmd-42"


def test_검증한_명령은_ROS_내부_전달_후에도_같다():
    original = parse_drive_command(payload())

    assert parse_internal_drive_command(drive_command_json(original)) == original


def test_손상된_내부_명령은_거부한다():
    broken = json.loads(drive_command_json(parse_drive_command(payload())))
    broken["duration_ms"] = -1

    with pytest.raises(CommandError) as error:
        parse_internal_drive_command(json.dumps(broken))

    assert error.value.code == "INVALID_INTERNAL_COMMAND"


# --- 유지 시간 (좌/우는 각도로, 직진은 시간으로) ---


@pytest.mark.parametrize("angular", [0.6, -0.6])
def test_제자리_회전은_durationMs가_아니라_목표_각도로_끊는다(angular):
    """버튼 한 번이 늘 같은 각도를 돌아야 방향을 가늠하며 조작할 수 있다."""
    command = parse_drive_command(
        payload(direction="LEFT", linearMps=0.0, angularRps=angular, durationMs=600)
    )

    # 22.5도(0.3927rad)를 0.6rad/s 로 돌면 약 0.654초. 서버의 600ms 가 아니다.
    assert hold_seconds(command, TURN_22_5) == pytest.approx(0.6545, abs=1e-3)


def test_회전_각속도가_빠르면_유지_시간이_짧아진다():
    """각도가 고정이므로 시간은 각속도에 반비례해야 한다."""
    slow = parse_drive_command(payload(linearMps=0.0, angularRps=0.3))
    fast = parse_drive_command(payload(linearMps=0.0, angularRps=1.2))

    assert hold_seconds(slow, TURN_22_5) == pytest.approx(
        4 * hold_seconds(fast, TURN_22_5)
    )


@pytest.mark.parametrize("linear", [0.12, -0.12])
def test_직진_후진은_서버가_준_durationMs를_그대로_쓴다(linear):
    command = parse_drive_command(
        payload(linearMps=linear, angularRps=0.0, durationMs=600)
    )

    assert hold_seconds(command, TURN_22_5) == pytest.approx(0.6)


def test_직진하며_도는_명령은_시간으로_끊는다():
    """각도로 끊는 건 제자리 회전뿐이다. 곡선 주행은 이동 거리가 얽혀 있어
    각도만으로 유지 시간을 정할 수 없다."""
    command = parse_drive_command(
        payload(linearMps=0.12, angularRps=0.6, durationMs=600)
    )

    assert hold_seconds(command, TURN_22_5) == pytest.approx(0.6)


def test_STOP은_유지_시간이_0이다():
    command = parse_drive_command(
        payload(direction="STOP", linearMps=0.0, angularRps=0.0, durationMs=0)
    )

    assert hold_seconds(command, TURN_22_5) == 0.0


def test_DriveCommand는_불변이다():
    command = DriveCommand(REQUEST_ID, 0.12, 0.0, 600, "FORWARD")

    with pytest.raises(Exception):
        command.linear_mps = 0.5
