"""서버와 주고받는 MQTT 메시지 형식.

ROS에 의존하지 않는 순수 파이썬 모듈입니다. 브로커 없이 CI에서 검증됩니다.

기준 문서는 **docs/DEVICE-MQTT.md** 입니다. 서버 팀이 관리하며 이 코드보다
권위가 있습니다. 어긋나면 문서를 따르세요.

서버 enum 과 문자열이 정확히 일치해야 합니다. 한 글자만 달라도 서버가
메시지를 버리고, 그게 조용히 일어나서 원인을 찾기 어렵습니다.

시각은 항상 UTC 로 보냅니다. 로봇과 서버의 시간대가 다르면 일기 생성이나
성장 기록의 시간 순서가 뒤섞입니다. 서버는 **현재보다 10분 이상 미래인
시각을 버리므로** 젯슨의 NTP 동기화가 필요합니다.
"""

import json
import math
import uuid
from datetime import datetime, timezone


class SensorType:
    """서버 SensorType enum 과 1:1 대응 (DEVICE-MQTT.md 5절).

    배터리는 여기 없습니다. 이 토픽으로 오는 값은 식물에 귀속되어 저장되는데
    배터리는 로봇의 속성이라 들어갈 자리가 없습니다. 전용 토픽을 씁니다.
    """

    TEMPERATURE = "TEMPERATURE"
    HUMIDITY = "HUMIDITY"
    SOIL_MOISTURE = "SOIL_MOISTURE"
    ILLUMINANCE = "ILLUMINANCE"


class SensorUnit:
    """서버 SensorUnit enum 과 1:1 대응."""

    CELSIUS = "CELSIUS"
    PERCENT = "PERCENT"
    LUX = "LUX"


class RobotState:
    """서버 RobotState enum 과 1:1 대응 (DEVICE-MQTT.md 7절).

    potner_mission.mission_manager_node.MissionState 와 이름이 같아야 합니다.
    상태 문자열을 그대로 실어 보내기 때문입니다.
    """

    IDLE = "IDLE"
    NAVIGATING = "NAVIGATING"
    DOCKING = "DOCKING"
    SERVICING = "SERVICING"
    GREETING = "GREETING"


class Expression:
    """디스플레이 표정 (DEVICE-MQTT.md 9절). 서버 -> 젯슨.

    ★ 서버가 값을 늘릴 수 있습니다. 모르는 값이 왔다고 로봇이 죽거나 표정이
      사라지면 안 되므로, 아래 목록에 없는 값은 DEFAULT_EXPRESSION 으로
      떨어뜨립니다. 그래야 서버가 표정을 추가할 때 로봇을 안 고칩니다.
    """

    VERY_HAPPY = "VERY_HAPPY"
    HAPPY = "HAPPY"
    NEUTRAL = "NEUTRAL"
    SAD = "SAD"


KNOWN_EXPRESSIONS = frozenset(
    value for name, value in vars(Expression).items() if not name.startswith("_")
)

DEFAULT_EXPRESSION = Expression.NEUTRAL


UNIT_FOR_TYPE = {
    SensorType.TEMPERATURE: SensorUnit.CELSIUS,
    SensorType.HUMIDITY: SensorUnit.PERCENT,
    SensorType.SOIL_MOISTURE: SensorUnit.PERCENT,
    SensorType.ILLUMINANCE: SensorUnit.LUX,
}

# 종류별 허용 범위 (DEVICE-MQTT.md 5절). 상한이 없으면 None.
# 범위를 벗어난 값은 서버가 조용히 버리므로 발행 전에 걸러냅니다.
VALUE_RANGE = {
    SensorType.TEMPERATURE: (-40.0, 85.0),
    SensorType.HUMIDITY: (0.0, 100.0),
    SensorType.SOIL_MOISTURE: (0.0, 100.0),
    SensorType.ILLUMINANCE: (0.0, None),
}

# 로봇이 발행하는 것. 온도와 습도는 스테이션이 재서 서버로 올립니다.
ROBOT_SENSOR_TYPES = (SensorType.SOIL_MOISTURE, SensorType.ILLUMINANCE)

ROBOT_STATES = frozenset(
    value for name, value in vars(RobotState).items() if not name.startswith("_")
)


def sensor_topic(device_id: str) -> str:
    return f"potner/device/{device_id}/sensor/telemetry"


def heartbeat_topic(device_id: str) -> str:
    return f"potner/device/{device_id}/status/heartbeat"


def state_topic(device_id: str) -> str:
    return f"potner/device/{device_id}/status/state"


def battery_topic(device_id: str) -> str:
    return f"potner/device/{device_id}/status/battery"


def command_topic(device_id: str) -> str:
    """서버가 내려보내는 명령. 브로커 ACL 이 이 경로만 읽기 허용합니다.

        pattern read potner/device/%u/command/#

    그래서 서버에서 받을 것은 모두 이 아래에 있어야 합니다.
    """
    return f"potner/device/{device_id}/command/#"


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
    묶어 보내지 않습니다. messageId 가 같으면 1건만 저장되고 나머지는
    조용히 사라집니다.

    Args:
        sensor_type: SensorType 의 값
        value: 측정값. 종류별 허용 범위 안이어야 합니다
        measured_at: 측정 시각. 발행 시각이 아니라 실제로 읽은 시각
        message_id: 생략하면 UUID 를 새로 만듭니다

    Raises:
        ValueError: 서버 enum 에 없는 종류이거나 값이 허용 범위를 벗어날 때
    """
    if sensor_type not in UNIT_FOR_TYPE:
        raise ValueError(f"서버 enum 에 없는 센서 종류: {sensor_type!r}")

    low, high = VALUE_RANGE[sensor_type]
    if value < low or (high is not None and value > high):
        limit = "이상" if high is None else f"~ {high}"
        raise ValueError(
            f"{sensor_type} 값이 허용 범위를 벗어났습니다: {value} "
            f"(허용 {low} {limit})"
        )

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
    """기기가 살아 있다는 신호. 90초간 없으면 서버가 OFFLINE 으로 표시합니다."""
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
    """로봇의 현재 행동 상태 (DEVICE-MQTT.md 7절).

    같은 상태를 반복해 보내도 됩니다. 서버 처리가 멱등하고, 실제로 바뀔 때만
    "바뀐 시각"을 갱신합니다.

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


def parse_expression_command(payload):
    """서버가 내려보낸 표정 명령을 해석합니다 (DEVICE-MQTT.md 9절).

    **예외를 내지 않습니다.** 브로커에서 오는 값이라 형식을 신뢰할 수 없고,
    표정을 그리는 일이 실패해서 로봇이 멈추면 안 됩니다. 해석할 수 없으면
    기본 표정을 돌려줍니다.

    같은 값이 30초마다 반복해서 옵니다. 중복을 걸러낼 필요 없이 마지막 값을
    그리면 되고, 노드를 재시작해도 한 주기 안에 표정을 되찾습니다.

    Args:
        payload: JSON 문자열 또는 bytes

    Returns:
        (expression, reason) — expression 은 항상 KNOWN_EXPRESSIONS 안의 값.
        reason 은 문자열이거나 None
    """
    if isinstance(payload, (bytes, bytearray)):
        payload = payload.decode("utf-8", errors="ignore")

    try:
        body = json.loads(payload)
    except (ValueError, TypeError):
        return DEFAULT_EXPRESSION, None

    if not isinstance(body, dict):
        return DEFAULT_EXPRESSION, None

    expression = body.get("expression")
    if expression not in KNOWN_EXPRESSIONS:
        # 서버가 표정을 추가했을 수 있습니다. 로그로 알 수 있게 사유는 남깁니다.
        return DEFAULT_EXPRESSION, body.get("reason")

    reason = body.get("reason")
    return expression, reason if isinstance(reason, str) else None


def battery_message(
    device_id: str, percent: float, measured_at: datetime, message_id: str = None
) -> str:
    """배터리 잔량 (DEVICE-MQTT.md 8절). 젯슨만 보냅니다.

    센서 토픽이 아니라 전용 토픽을 쓰는 이유는, 센서 값은 식물에 귀속되어
    저장되는데 배터리는 로봇의 속성이라 그 테이블에 자리가 없기 때문입니다.

    서버는 정수 0~100 만 받고 **범위를 벗어나면 버립니다.** 0 이나 100 으로
    깎지 않는 것은 센서 고장을 숨기지 않으려는 것이라, 여기서도 깎지 않고
    예외를 냅니다.

    Raises:
        ValueError: 내림한 값이 0~100 을 벗어날 때
    """
    floored = math.floor(percent)
    if not 0 <= floored <= 100:
        raise ValueError(f"배터리 잔량이 0~100 을 벗어났습니다: {percent} -> {floored}")

    return json.dumps(
        {
            "messageId": message_id or str(uuid.uuid4()),
            "deviceId": device_id,
            "batteryPercent": floored,
            "measuredAt": iso_utc(measured_at),
        },
        ensure_ascii=False,
    )
