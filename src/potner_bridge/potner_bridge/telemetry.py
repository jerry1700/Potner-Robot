"""서버와 주고받는 MQTT 메시지 형식.

ROS에 의존하지 않는 순수 파이썬 모듈입니다. 브로커 없이 CI에서 검증됩니다.

서버(Spring Boot)의 enum 과 문자열이 정확히 일치해야 합니다. 한 글자만
달라도 서버가 메시지를 버리고, 그게 조용히 일어나서 원인을 찾기 어렵습니다.

    com.potner.sensor.domain.SensorType
    com.potner.sensor.domain.SensorUnit

시각은 항상 UTC 로 보냅니다. 로봇과 서버의 시간대가 다르면 일기 생성이나
성장 기록의 시간 순서가 뒤섞입니다.
"""

import json
import uuid
from datetime import datetime, timezone


class SensorType:
    """서버 SensorType enum 과 1:1 대응."""

    TEMPERATURE = "TEMPERATURE"
    HUMIDITY = "HUMIDITY"
    SOIL_MOISTURE = "SOIL_MOISTURE"
    ILLUMINANCE = "ILLUMINANCE"

    # 4S 젯슨팩 기준입니다. 팩이 두 개지만 젯슨은 15~25W 를 계속 먹고
    # 모터는 간헐적으로만 도니, 젯슨팩이 먼저 바닥납니다. 그래서 이 쪽만
    # 재고 3S 모터팩은 측정하지 않습니다.
    BATTERY = "BATTERY"


class SensorUnit:
    """서버 SensorUnit enum 과 1:1 대응."""

    CELSIUS = "CELSIUS"
    PERCENT = "PERCENT"
    LUX = "LUX"


class RobotState:
    """서버 RobotState enum 과 1:1 대응.

    potner_mission.mission_manager_node.MissionState 와 이름이 같아야
    합니다. 상태 문자열을 그대로 실어 보내기 때문입니다.
    """

    IDLE = "IDLE"
    NAVIGATING = "NAVIGATING"
    DOCKING = "DOCKING"
    SERVICING = "SERVICING"
    GREETING = "GREETING"


# 센서 종류별 단위. 서버가 unit 을 함께 받으므로 여기서 확정해 보냅니다.
UNIT_FOR_TYPE = {
    SensorType.TEMPERATURE: SensorUnit.CELSIUS,
    SensorType.HUMIDITY: SensorUnit.PERCENT,
    SensorType.SOIL_MOISTURE: SensorUnit.PERCENT,
    SensorType.ILLUMINANCE: SensorUnit.LUX,
    SensorType.BATTERY: SensorUnit.PERCENT,
}

# 로봇이 발행하는 것. 온도와 습도는 스테이션이 재서 올립니다.
ROBOT_SENSOR_TYPES = (
    SensorType.SOIL_MOISTURE,
    SensorType.ILLUMINANCE,
    SensorType.BATTERY,
)

ROBOT_STATES = frozenset(
    value for name, value in vars(RobotState).items() if not name.startswith("_")
)


def sensor_topic(device_id: str) -> str:
    return f"potner/device/{device_id}/sensor/telemetry"


def heartbeat_topic(device_id: str) -> str:
    return f"potner/device/{device_id}/status/heartbeat"


def state_topic(device_id: str) -> str:
    return f"potner/device/{device_id}/status/state"


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def iso_utc(moment: datetime) -> str:
    """서버가 기대하는 ISO 8601 UTC 문자열로 바꿉니다.

    예) 2026-07-23T08:00:00Z

    시간대 정보가 없는 datetime 은 UTC 로 간주합니다. 로컬 시각을 그대로
    넣으면 9시간 어긋난 기록이 쌓입니다.
    """
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sensor_message(
    device_id: str,
    sensor_type: str,
    value: float,
    measured_at: datetime,
    message_id: str = None,
) -> str:
    """센서 측정값 한 건을 JSON 문자열로 만듭니다.

    서버는 측정값 하나당 메시지 하나를 받습니다. 여러 센서를 한 메시지에
    묶어 보내지 않습니다.

    Args:
        sensor_type: SensorType 의 값
        value: 측정값
        measured_at: 측정 시각. 발행 시각이 아니라 실제로 읽은 시각
        message_id: 생략하면 UUID 를 새로 만듭니다

    Raises:
        ValueError: 서버 enum 에 없는 sensor_type 일 때
    """
    if sensor_type not in UNIT_FOR_TYPE:
        raise ValueError(f"서버 enum 에 없는 센서 종류: {sensor_type!r}")

    return json.dumps(
        {
            "messageId": message_id or str(uuid.uuid4()),
            "deviceId": device_id,
            "sensorType": sensor_type,
            "value": value,
            "unit": UNIT_FOR_TYPE[sensor_type],
            "measuredAt": iso_utc(measured_at),
        },
        ensure_ascii=False,
    )


def heartbeat_message(
    device_id: str, sent_at: datetime, message_id: str = None
) -> str:
    """로봇이 살아 있다는 신호를 JSON 문자열로 만듭니다."""
    return json.dumps(
        {
            "messageId": message_id or str(uuid.uuid4()),
            "deviceId": device_id,
            "sentAt": iso_utc(sent_at),
        },
        ensure_ascii=False,
    )


def state_message(
    device_id: str, state: str, changed_at: datetime, message_id: str = None
) -> str:
    """로봇의 현재 상태를 JSON 문자열로 만듭니다.

    앱에서 "로봇이 지금 무엇을 하는지" 보여주기 위한 것입니다.

    Raises:
        ValueError: RobotState 에 없는 상태일 때
    """
    if state not in ROBOT_STATES:
        raise ValueError(f"서버 enum 에 없는 상태: {state!r}")

    return json.dumps(
        {
            "messageId": message_id or str(uuid.uuid4()),
            "deviceId": device_id,
            "state": state,
            "changedAt": iso_utc(changed_at),
        },
        ensure_ascii=False,
    )


def parse_sensor_message(payload: str):
    """다른 기기(스테이션)가 보낸 센서 메시지를 해석합니다.

    로봇은 대기 온도를 직접 재지 않고 스테이션이 올린 값을 씁니다.
    스테이션도 같은 형식을 쓰므로 이 함수로 함께 처리합니다.

    Returns:
        (sensor_type, value). 형식이 어긋나면 (None, None)
    """
    try:
        data = json.loads(payload)
    except (json.JSONDecodeError, TypeError):
        return None, None

    if not isinstance(data, dict):
        return None, None

    sensor_type = data.get("sensorType")
    if sensor_type not in UNIT_FOR_TYPE:
        return None, None

    try:
        return sensor_type, float(data["value"])
    except (KeyError, TypeError, ValueError):
        return None, None
