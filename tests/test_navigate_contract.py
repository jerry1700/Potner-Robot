"""서버와 Jetson 사이 이동 명령 MQTT 계약 검증."""

import json
import math
from uuid import UUID

import pytest

from potner_bridge.command_result import NAVIGATE, CommandError
from potner_bridge.navigate_contract import (
    DOCKING_MARKERS,
    NavigateCommand,
    navigate_command_json,
    navigate_result_message,
    navigate_result_topic,
    parse_internal_navigate_command,
    parse_navigate_command,
)

REQUEST_ID = "3ff0d4af-9217-4473-b7a4-f8ff8d420daa"


def payload(**overrides):
    body = {
        "destination": "WATER_STATION",
        "x": 1.250,
        "y": -0.480,
        "yaw": 1.5708,
        "requestId": REQUEST_ID,
    }
    body.update(overrides)
    for key in [name for name, value in body.items() if value is _MISSING]:
        del body[key]
    return json.dumps(body)


class _Missing:
    pass


_MISSING = _Missing()


def test_이동_명령을_목적지와_지도_좌표로_해석한다():
    command = parse_navigate_command(payload().encode())

    assert command.destination == "WATER_STATION"
    assert (command.pose.x, command.pose.y, command.pose.yaw) == (
        1.250,
        -0.480,
        1.5708,
    )
    assert command.request_id == REQUEST_ID
    assert command.command_name == NAVIGATE


def test_지도_원점도_정상_좌표다():
    """서버는 미설정을 NULL 로 두고 좌표 없는 위치로는 명령을 안 보낸다.

    0,0,0 은 "미설정" 이 아니라 지도 원점이라는 실제 좌표다. 로봇 파라미터
    쪽 규약(전부 0 이면 미설정)을 여기로 옮기면 원점에 세운 스테이션으로
    영영 가지 못한다.
    """
    command = parse_navigate_command(payload(x=0.0, y=0.0, yaw=0.0))

    assert (command.pose.x, command.pose.y, command.pose.yaw) == (0.0, 0.0, 0.0)


@pytest.mark.parametrize("destination", ["WATER_STATION", "HOME", "SUNLIGHT", "GREETING"])
def test_서버가_보내는_목적지_네_종류를_모두_받는다(destination):
    assert parse_navigate_command(payload(destination=destination)).destination == (
        destination
    )


@pytest.mark.parametrize(
    "body,code",
    [
        ("not-json", "INVALID_JSON"),
        ("[]", "INVALID_PAYLOAD"),
        (payload(destination="CHARGING"), "INVALID_DESTINATION"),
        (payload(destination=_MISSING), "INVALID_DESTINATION"),
        (payload(requestId=_MISSING), "INVALID_IDENTIFIER"),
        (payload(requestId=""), "INVALID_IDENTIFIER"),
        (payload(requestId="x" * 101), "INVALID_IDENTIFIER"),
        (payload(x=_MISSING), "INVALID_POSE"),
        (payload(y="1.0"), "INVALID_POSE"),
        (payload(yaw=math.pi + 0.01), "INVALID_POSE"),
    ],
)
def test_갈_곳을_모르는_명령은_거부한다(body, code):
    with pytest.raises(CommandError) as error:
        parse_navigate_command(body)

    assert error.value.code == code


def test_거절할_때도_requestId를_건져_회신할_수_있다():
    """회신이 없으면 서버가 제한시간까지 기다렸다가 TIMED_OUT 으로 끊는다."""
    with pytest.raises(CommandError) as error:
        parse_navigate_command(payload(destination="CHARGING"))

    assert error.value.request_id == REQUEST_ID


def test_requestId가_UUID가_아니어도_받는다():
    """서버 DTO 가 @Size(max = 100) String 이라 UUID 가 아닌 값도 유효하다.

    로봇의 몫은 받은 값을 그대로 되돌리는 것이지 서버 식별자의 형식을
    정하는 것이 아니다.
    """
    command = parse_navigate_command(payload(requestId="cmd-42"))

    assert command.request_id == "cmd-42"


def test_검증한_명령은_ROS_내부_전달_후에도_같다():
    original = parse_navigate_command(payload())

    assert parse_internal_navigate_command(navigate_command_json(original)) == original


def test_손상된_내부_명령은_거부한다():
    broken = json.loads(navigate_command_json(parse_navigate_command(payload())))
    broken["pose"]["yaw"] = 99.0

    with pytest.raises(CommandError) as error:
        parse_internal_navigate_command(json.dumps(broken))

    assert error.value.code == "INVALID_INTERNAL_COMMAND"


# --- 결과 회신 ---


def test_회신_토픽은_result_아래다():
    """command/ 아래로 쓰면 브로커 ACL 이 막아 흔적 없이 사라진다."""
    topic = navigate_result_topic("jetson-01")

    assert topic == "potner/device/jetson-01/result/navigate"
    assert "/result/" in topic


def test_결과_JSON이_서버_CommandResultMessage와_맞는다():
    result = json.loads(
        navigate_result_message(
            "jetson-01",
            REQUEST_ID,
            "ERROR",
            error="도킹 실패: 마커를 찾지 못함",
            code="DOCKING_FAILED",
            message_id="0b180b7a-41c6-4ad1-8519-5ebf89ddf6df",
        )
    )

    assert result == {
        "messageId": "0b180b7a-41c6-4ad1-8519-5ebf89ddf6df",
        "deviceId": "jetson-01",
        "requestId": REQUEST_ID,
        "status": "ERROR",
        "error": "도킹 실패: 마커를 찾지 못함",
        "code": "DOCKING_FAILED",
    }


def test_requestId는_반향하고_messageId는_회신마다_새로_만든다():
    """messageId 를 재사용하면 서버가 중복으로 보고 버린다."""
    first = json.loads(navigate_result_message("jetson-01", REQUEST_ID, "OK"))
    second = json.loads(navigate_result_message("jetson-01", REQUEST_ID, "OK"))

    assert first["requestId"] == second["requestId"] == REQUEST_ID
    assert first["messageId"] != second["messageId"]
    UUID(first["messageId"])  # 서버 DTO 가 @NotNull UUID 다


def test_토픽과_페이로드의_deviceId는_같은_값에서_나온다():
    device_id = "POTNER-D2-JT"
    topic = navigate_result_topic(device_id)
    body = json.loads(navigate_result_message(device_id, REQUEST_ID, "OK"))

    # 서버는 둘을 String.equals 로 대조한다. 대소문자가 어긋나면 버린다.
    assert topic.split("/")[2] == body["deviceId"]


def test_OK에는_실패_사유를_실을_수_없다():
    with pytest.raises(ValueError):
        navigate_result_message("jetson-01", REQUEST_ID, "OK", error="어딘가 이상함")


def test_서버가_모르는_상태는_만들지_않는다():
    """서버는 OK/ERROR/BUSY 외의 값을 INVALID_STATUS 로 폐기한다."""
    with pytest.raises(ValueError):
        navigate_result_message("jetson-01", REQUEST_ID, "DONE")


# --- 도킹 매핑 ---


def test_정밀_도킹은_급수_스테이션에서만_한다():
    """넷 중 물리 장치가 있는 곳은 급수 스테이션뿐이다.

    나머지는 SLAM 지도 위의 좌표일 뿐이라 볼 마커가 없다. HOME 을 충전
    독(마커 1)과 같은 자리로 단정하면, 서버가 아직 정하지 않은 결정을
    로봇 코드에 박아넣는 것이 된다.
    """
    assert DOCKING_MARKERS == {"WATER_STATION": 2}

    for destination in ("HOME", "SUNLIGHT", "GREETING"):
        command = parse_navigate_command(payload(destination=destination))
        assert command.marker_id is None

    station = parse_navigate_command(payload(destination="WATER_STATION"))
    assert station.marker_id == 2


def test_NavigateCommand는_불변이다():
    command = NavigateCommand(REQUEST_ID, "HOME", parse_navigate_command(payload()).pose)

    with pytest.raises(Exception):
        command.destination = "SUNLIGHT"
