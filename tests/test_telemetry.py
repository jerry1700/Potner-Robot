"""MQTT 메시지 형식 검증.

서버 enum 과 문자열이 한 글자라도 어긋나면 서버가 메시지를 조용히 버립니다.
브로커 없이 여기서 잡습니다.
"""

import json
from datetime import datetime, timedelta, timezone

import pytest

from potner_bridge.telemetry import (
    ROBOT_SENSOR_TYPES,
    ROBOT_STATES,
    UNIT_FOR_TYPE,
    RobotState,
    SensorType,
    SensorUnit,
    heartbeat_message,
    heartbeat_topic,
    iso_utc,
    parse_sensor_message,
    sensor_message,
    sensor_topic,
    state_message,
    state_topic,
)

MOMENT = datetime(2026, 7, 23, 8, 0, 0, tzinfo=timezone.utc)


def test_토픽_형식():
    assert sensor_topic("jetson-01") == "potner/device/jetson-01/sensor/telemetry"
    assert heartbeat_topic("jetson-01") == "potner/device/jetson-01/status/heartbeat"


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


def test_배터리는_퍼센트로_보낸다():
    """4S 젯슨팩 기준입니다. 3S 모터팩은 측정하지 않습니다."""
    payload = json.loads(
        sensor_message("jetson-01", SensorType.BATTERY, 78.0, MOMENT, "uuid-3")
    )

    assert payload["sensorType"] == "BATTERY"
    assert payload["unit"] == "PERCENT"
    assert payload["value"] == 78.0


def test_로봇이_발행하는_종류는_세_가지다():
    """온도·습도는 스테이션이 잽니다."""
    assert set(ROBOT_SENSOR_TYPES) == {
        SensorType.SOIL_MOISTURE,
        SensorType.ILLUMINANCE,
        SensorType.BATTERY,
    }


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


def test_스테이션이_보낸_메시지를_해석한다():
    """로봇은 대기 온도를 직접 재지 않고 스테이션이 올린 값을 씁니다."""
    payload = sensor_message("raspberry-01", SensorType.TEMPERATURE, 24.3, MOMENT)
    assert parse_sensor_message(payload) == (SensorType.TEMPERATURE, 24.3)


@pytest.mark.parametrize(
    "payload",
    [
        "",
        "not json",
        "[]",
        '{"sensorType": "UNKNOWN", "value": 1}',
        '{"sensorType": "TEMPERATURE"}',
        '{"sensorType": "TEMPERATURE", "value": "뜨거움"}',
        '{"value": 1}',
    ],
)
def test_망가진_메시지는_None_을_돌려준다(payload):
    """브로커에 다른 팀이 잘못 올린 값이 섞여 들어와도 노드가 죽지 않아야 합니다."""
    assert parse_sensor_message(payload) == (None, None)


def test_한글이_이스케이프되지_않는다():
    """서버 로그에서 읽을 수 있어야 합니다."""
    payload = sensor_message("한글-01", SensorType.ILLUMINANCE, 1.0, MOMENT)
    assert "한글-01" in payload
