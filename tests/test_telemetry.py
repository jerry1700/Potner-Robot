"""MQTT 메시지 형식 검증.

서버 enum 과 문자열이 한 글자라도 어긋나면 서버가 메시지를 조용히 버립니다.
브로커 없이 여기서 잡습니다.
"""

import json
from datetime import datetime, timedelta, timezone

import pytest

from potner_bridge.telemetry import (
    DEFAULT_EXPRESSION,
    KNOWN_EXPRESSIONS,
    ROBOT_SENSOR_TYPES,
    ROBOT_STATES,
    UNIT_FOR_TYPE,
    VALUE_RANGE,
    RobotState,
    SensorType,
    SensorUnit,
    battery_message,
    battery_topic,
    command_topic,
    parse_expression_command,
    heartbeat_message,
    heartbeat_topic,
    iso_utc,
    sensor_message,
    sensor_topic,
    state_message,
    state_topic,
)

MOMENT = datetime(2026, 7, 23, 8, 0, 0, tzinfo=timezone.utc)


def test_토픽_형식():
    assert sensor_topic("jetson-01") == "potner/device/jetson-01/sensor/telemetry"
    assert heartbeat_topic("jetson-01") == "potner/device/jetson-01/status/heartbeat"
    assert state_topic("jetson-01") == "potner/device/jetson-01/status/state"
    assert battery_topic("jetson-01") == "potner/device/jetson-01/status/battery"
    assert command_topic("jetson-01") == "potner/device/jetson-01/command/#"


@pytest.mark.parametrize(
    "topic",
    [
        sensor_topic("jetson-01"),
        heartbeat_topic("jetson-01"),
        state_topic("jetson-01"),
        battery_topic("jetson-01"),
    ],
)
def test_모든_토픽이_ACL_이_허용하는_경로_안에_있다(topic):
    """브로커 ACL 이 자기 기기 아래의 sensor/status/result 만 쓰기 허용합니다.

        pattern write potner/device/%u/sensor|status|result/#

    벗어난 토픽은 발행이 조용히 버려지고 양쪽 로그에 아무것도 남지 않아
    가장 찾기 어렵습니다. 그래서 형식을 여기서 못박습니다.
    """
    head, _, tail = topic.partition("/status/")
    if not tail:
        head, _, tail = topic.partition("/sensor/")

    assert head == "potner/device/jetson-01", f"ACL 범위를 벗어난 토픽: {topic}"


def test_센서_메시지가_명세와_일치한다():
    payload = json.loads(
        sensor_message("jetson-01", SensorType.ILLUMINANCE, 24.3, MOMENT, "uuid-1")
    )

    assert payload == {
        "messageId": "uuid-1",
        "deviceId": "jetson-01",
        "sensorType": "ILLUMINANCE",
        "value": 24.3,
        "unit": "LUX",
        "measuredAt": "2026-07-23T08:00:00Z",
    }


def test_하트비트_메시지가_명세와_일치한다():
    payload = json.loads(heartbeat_message("jetson-01", MOMENT, "uuid-2"))

    assert payload == {
        "messageId": "uuid-2",
        "deviceId": "jetson-01",
        "sentAt": "2026-07-23T08:00:00Z",
    }


def test_messageId_를_생략하면_매번_다른_UUID():
    first = json.loads(sensor_message("jetson-01", SensorType.ILLUMINANCE, 1.0, MOMENT))
    second = json.loads(sensor_message("jetson-01", SensorType.ILLUMINANCE, 1.0, MOMENT))

    assert first["messageId"] != second["messageId"]
    assert len(first["messageId"]) == 36  # UUID 표준 문자열 길이


def test_시각은_항상_UTC_로_바뀐다():
    """로컬 시각을 그대로 보내면 9시간 어긋난 기록이 쌓입니다."""
    seoul = timezone(timedelta(hours=9))
    local = datetime(2026, 7, 23, 17, 0, 0, tzinfo=seoul)

    assert iso_utc(local) == "2026-07-23T08:00:00Z"


def test_시간대가_없으면_UTC_로_본다():
    assert iso_utc(datetime(2026, 7, 23, 8, 0, 0)) == "2026-07-23T08:00:00Z"


def test_시각_형식은_초까지_그리고_Z_로_끝난다():
    stamp = iso_utc(datetime(2026, 1, 2, 3, 4, 5, 678901, tzinfo=timezone.utc))
    assert stamp == "2026-01-02T03:04:05Z"


@pytest.mark.parametrize("sensor_type", list(UNIT_FOR_TYPE))
def test_모든_센서_종류에_단위가_정해져_있다(sensor_type):
    payload = json.loads(sensor_message("jetson-01", sensor_type, 1.0, MOMENT))
    assert payload["unit"] in vars(SensorUnit).values()


def test_단위_대응이_명세와_맞는다():
    assert UNIT_FOR_TYPE[SensorType.TEMPERATURE] == SensorUnit.CELSIUS
    assert UNIT_FOR_TYPE[SensorType.HUMIDITY] == SensorUnit.PERCENT
    assert UNIT_FOR_TYPE[SensorType.SOIL_MOISTURE] == SensorUnit.PERCENT
    assert UNIT_FOR_TYPE[SensorType.ILLUMINANCE] == SensorUnit.LUX


def test_서버_enum_에_없는_종류는_거부한다():
    """오타나 임의로 만든 타입을 보내면 서버가 조용히 버립니다."""
    with pytest.raises(ValueError, match="센서 종류"):
        sensor_message("jetson-01", "PRESSURE", 80.0, MOMENT)

    with pytest.raises(ValueError):
        sensor_message("jetson-01", "illuminance", 1.0, MOMENT)  # 소문자


def test_배터리는_센서_종류가_아니다():
    """센서값은 식물에 귀속되어 저장되는데 배터리는 로봇의 속성입니다.

    BATTERY 를 sensorType 으로 보내면 서버 enum 에 없어서 조용히 버려집니다.
    전용 토픽 status/battery 를 씁니다.
    """
    assert not hasattr(SensorType, "BATTERY")

    with pytest.raises(ValueError, match="센서 종류"):
        sensor_message("jetson-01", "BATTERY", 78.0, MOMENT)


def test_로봇이_발행하는_센서는_두_가지다():
    """온도·습도는 스테이션이 잽니다. 배터리는 센서가 아닙니다."""
    assert set(ROBOT_SENSOR_TYPES) == {
        SensorType.SOIL_MOISTURE,
        SensorType.ILLUMINANCE,
    }


def test_배터리_메시지가_명세와_일치한다():
    """4S 젯슨팩 기준입니다. 3S 모터팩은 측정하지 않습니다."""
    payload = json.loads(battery_message("jetson-01", 78.0, MOMENT, "uuid-5"))

    assert payload == {
        "messageId": "uuid-5",
        "deviceId": "jetson-01",
        "batteryPercent": 78,
        "measuredAt": "2026-07-23T08:00:00Z",
    }


def test_배터리_잔량은_정수로_내림한다():
    """서버가 실수를 받으면 내려서 저장하므로 미리 맞춰 보냅니다."""
    payload = json.loads(battery_message("jetson-01", 78.9, MOMENT))

    assert payload["batteryPercent"] == 78
    assert isinstance(payload["batteryPercent"], int)


@pytest.mark.parametrize("percent", [-0.5, -1.0, 101.0, 255.0])
def test_범위를_벗어난_배터리는_깎지_않고_거부한다(percent):
    """0 이나 100 으로 깎으면 값을 만드는 쪽의 버그가 숨습니다.

    단위 착각이나 셀 수 오설정으로 255% 가 나왔을 때 100 으로 깎아 보내면
    앱에는 "완충" 으로 보여서 아무도 눈치채지 못합니다.

    다만 plant_conversions.battery_percent 는 리튬이온 곡선 끝에서 이미
    0~100 으로 자르므로, 실제 운영 경로에서는 이 검사가 걸리지 않습니다.
    여기서 막는 것은 그 함수를 우회하거나 바꿀 때의 실수입니다.
    """
    with pytest.raises(ValueError, match="0~100"):
        battery_message("jetson-01", percent, MOMENT)


@pytest.mark.parametrize("percent", [0.0, 0.4, 50.0, 100.0, 100.5])
def test_경계값_배터리는_통과한다(percent):
    """범위 검사는 내림한 뒤 값에 걸립니다. 100.5 -> 100 은 유효합니다.

    반대로 -0.5 -> -1 은 거부됩니다. int() 로 자르면 -0.5 가 0 이 되어
    통과해버리므로 math.floor 를 써야 합니다.
    """
    battery_message("jetson-01", percent, MOMENT)


@pytest.mark.parametrize(
    "sensor_type,value",
    [
        (SensorType.TEMPERATURE, -40.1),
        (SensorType.TEMPERATURE, 85.1),
        (SensorType.HUMIDITY, -0.1),
        (SensorType.HUMIDITY, 100.1),
        (SensorType.SOIL_MOISTURE, -1.0),
        (SensorType.SOIL_MOISTURE, 101.0),
        (SensorType.ILLUMINANCE, -1.0),
    ],
)
def test_허용_범위를_벗어난_센서값은_거부한다(sensor_type, value):
    """서버가 조용히 버리므로 발행 전에 걸러야 고장을 알 수 있습니다."""
    with pytest.raises(ValueError, match="허용 범위"):
        sensor_message("jetson-01", sensor_type, value, MOMENT)


@pytest.mark.parametrize(
    "sensor_type,value",
    [
        (SensorType.TEMPERATURE, -40.0),
        (SensorType.TEMPERATURE, 85.0),
        (SensorType.HUMIDITY, 0.0),
        (SensorType.SOIL_MOISTURE, 100.0),
        (SensorType.ILLUMINANCE, 0.0),
        (SensorType.ILLUMINANCE, 65535.0),  # BH1750 최대 출력. 상한이 없습니다
    ],
)
def test_경계값_센서값은_통과한다(sensor_type, value):
    sensor_message("jetson-01", sensor_type, value, MOMENT)


def test_모든_센서_종류에_허용_범위가_정해져_있다():
    """종류를 추가하면서 범위를 빼먹으면 발행 시점에 KeyError 로 죽습니다."""
    assert set(VALUE_RANGE) == set(UNIT_FOR_TYPE)


def test_상태_메시지가_명세와_일치한다():
    payload = json.loads(
        state_message("jetson-01", RobotState.DOCKING, MOMENT, "uuid-4")
    )

    assert payload == {
        "messageId": "uuid-4",
        "deviceId": "jetson-01",
        "state": "DOCKING",
        "changedAt": "2026-07-23T08:00:00Z",
    }


def test_없는_상태는_거부한다():
    with pytest.raises(ValueError, match="상태"):
        state_message("jetson-01", "CHARGING", MOMENT)

    with pytest.raises(ValueError):
        state_message("jetson-01", "idle", MOMENT)  # 소문자


def test_상태_목록이_임무_상태_머신과_일치한다():
    """mission_manager 의 MissionState 이름을 그대로 실어 보냅니다.
    한쪽만 바꾸면 상태 발행이 예외로 막힙니다."""
    assert ROBOT_STATES == {
        "IDLE",
        "NAVIGATING",
        "DOCKING",
        "SERVICING",
        "GREETING",
    }


def test_한글이_이스케이프되지_않는다():
    """서버 로그에서 읽을 수 있어야 합니다."""
    payload = sensor_message("한글-01", SensorType.ILLUMINANCE, 1.0, MOMENT)
    assert "한글-01" in payload


def test_표정_명령을_해석한다():
    payload = '{"plantId":"abc","expression":"HAPPY","reason":"SUNLIGHT"}'
    assert parse_expression_command(payload) == ("HAPPY", "SUNLIGHT")


def test_표정_명령은_bytes_도_받는다():
    """paho 가 payload 를 bytes 로 줍니다."""
    payload = b'{"expression":"SAD","reason":"DRY"}'
    assert parse_expression_command(payload) == ("SAD", "DRY")


@pytest.mark.parametrize(
    "payload",
    [
        "",
        "not json",
        "[]",
        "null",
        '{"expression":"EXCITED"}',       # 서버가 값을 늘린 경우
        '{"expression":"happy"}',         # 소문자
        '{"expression":null}',
        "{}",
    ],
)
def test_해석할_수_없으면_기본_표정을_돌려준다(payload):
    """★ 예외를 내지 않아야 합니다.

    표정을 그리는 일이 실패해서 로봇이 멈추면 안 됩니다. 그리고 서버가 표정을
    추가할 때 로봇 코드를 고치지 않아도 되어야 합니다.
    """
    expression, _ = parse_expression_command(payload)
    assert expression == DEFAULT_EXPRESSION


def test_모르는_표정이어도_사유는_남긴다():
    """서버가 값을 늘렸다는 걸 로그로 알 수 있어야 합니다."""
    payload = '{"expression":"EXCITED","reason":"NEW_FEATURE"}'
    assert parse_expression_command(payload) == (DEFAULT_EXPRESSION, "NEW_FEATURE")


def test_사유가_문자열이_아니면_버린다():
    expression, reason = parse_expression_command('{"expression":"HAPPY","reason":42}')
    assert (expression, reason) == ("HAPPY", None)


def test_표정_목록이_명세의_네_가지다():
    assert KNOWN_EXPRESSIONS == {"VERY_HAPPY", "HAPPY", "NEUTRAL", "SAD"}
