"""서버 명령 결과 회신의 공통 계약.

귀가 마중(welcome_start/cancel)과 이동(navigate)이 같은 결과 형식을 씁니다.
서버가 ``potner/device/+/result/#`` 하나로 전부 받아 ``CommandResultMessage``
DTO 하나로 파싱하기 때문입니다. 그래서 결과 토픽과 결과 JSON 을 만드는
곳은 여기 하나여야 합니다 — 명령마다 따로 적으면 한쪽만 고쳐지고,
어긋난 쪽은 브로커도 서버도 에러를 돌려주지 않아 조용히 사라집니다.

★ 상태 문자열 세 개의 무게가 다릅니다.

  서버는 OK/ERROR/BUSY 를 **모두 종단 상태로** 봅니다
  (``DeviceCommandStatus.isTerminal``). 한 번 보내면 그 requestId 는 끝난
  것이 되어, 나중에 진짜 도착해서 OK 를 보내도 ``ALREADY_COMPLETED`` 로
  버려집니다. 자동 급수·촬영·말리기 체인은 OK 가 아닌 결과를 보면 그
  자리에서 멈춥니다 (``AutoWateringOrchestrator.onCommandCompleted``).

  그래서 **BUSY 는 다른 requestId 를 거절할 때만** 씁니다. QoS 1 은 같은
  메시지를 두 번 배달할 수 있는데, 그 재전송에 BUSY 를 보내면 지금
  수행하고 있는 명령을 스스로 실패로 확정해 체인을 끊습니다. 재전송이면
  아무 것도 보내지 않고 도착했을 때 한 번만 회신합니다.

  ``TIMED_OUT`` 은 종단이 아닙니다. 서버 제한시간(기본 120초)을 넘겨도
  늦게 도착한 회신이 그 위에 덮어쓰고 체인도 이어집니다. **늦더라도
  반드시 보내세요.**
"""

import json
import math
from dataclasses import dataclass
from typing import Optional, Union
from uuid import UUID, uuid4

WELCOME_START = "welcome_start"
WELCOME_CANCEL = "welcome_cancel"
NAVIGATE = "navigate"

# 서버 DeviceCommandType.commandName() 및 귀가 명령 이름과 1:1 입니다.
# 토픽의 마지막 조각이 되므로 대소문자까지 같아야 합니다.
COMMAND_NAMES = frozenset({WELCOME_START, WELCOME_CANCEL, NAVIGATE})

RESULT_STATUSES = {"OK", "ERROR", "BUSY"}

# requestId 최대 길이. 서버 CommandResultMessage 의 @Size(max = 100) 과 같습니다.
MAX_REQUEST_ID_LENGTH = 100


class CommandError(ValueError):
    """안전하게 실행할 수 없는 서버 명령.

    ``request_id`` 는 알아낼 수 있었으면 담깁니다. 거절도 회신해야 서버가
    제한시간까지 기다리지 않기 때문입니다.
    """

    def __init__(self, code: str, message: str, request_id: Optional[str] = None):
        super().__init__(message)
        self.code = code
        self.request_id = request_id


@dataclass(frozen=True)
class MapPose:
    """map 프레임 위 한 점. 단위는 m 과 rad."""

    x: float
    y: float
    yaw: float


def result_topic(device_id: str, command_name: str) -> str:
    """결과 회신 토픽.

    ``result/`` 아래여야 합니다. ``command/<name>/result`` 처럼 만들면
    브로커 ACL 이 장치의 ``command/#`` 쓰기를 막아 **양쪽 로그에 아무
    흔적 없이** 사라집니다. 라즈베리가 실제로 이 사고를 겪었습니다.
    """
    require_device_id(device_id)
    require_command_name(command_name)
    return f"potner/device/{device_id}/result/{command_name}"


def result_message(
    device_id: str,
    request_id: str,
    status: str,
    *,
    error: Optional[str] = None,
    code: Optional[str] = None,
    message_id: Optional[str] = None,
) -> str:
    """서버 ``CommandResultMessage`` 와 호환되는 JSON을 만든다.

    ``message_id`` 를 생략하면 새 UUID 를 만듭니다. 회신마다 새로 만들어야
    합니다 — 서버가 같은 messageId 를 중복으로 보고 버립니다.
    """
    require_device_id(device_id)
    require_request_id(request_id)
    if status not in RESULT_STATUSES:
        raise ValueError(f"지원하지 않는 결과 상태: {status}")
    if status == "OK" and (error is not None or code is not None):
        raise ValueError("OK 결과에는 error/code를 넣을 수 없습니다.")

    resolved_message_id = message_id or str(uuid4())
    require_uuid(resolved_message_id, "messageId")
    payload = {
        "messageId": resolved_message_id,
        "deviceId": device_id,
        "requestId": request_id,
        "status": status,
    }
    if error:
        payload["error"] = error
    if code:
        payload["code"] = code
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def result_envelope(
    command_name: str,
    request_id: str,
    status: str,
    *,
    error: Optional[str] = None,
    code: Optional[str] = None,
) -> str:
    """mission_manager 에서 MQTT bridge 로 보내는 ROS 내부 결과."""
    require_request_id(request_id)
    require_command_name(command_name)
    if status not in RESULT_STATUSES:
        raise ValueError(f"지원하지 않는 결과 상태: {status}")
    return json.dumps(
        {
            "commandName": command_name,
            "requestId": request_id,
            "status": status,
            "error": error,
            "code": code,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def parse_result_envelope(payload: Union[str, bytes]) -> dict:
    try:
        if isinstance(payload, bytes):
            payload = payload.decode("utf-8")
        body = json.loads(payload)
        command_name = body["commandName"]
        request_id = body["requestId"]
        status = body["status"]
        require_request_id(request_id)
        require_command_name(command_name)
        if status not in RESULT_STATUSES:
            raise ValueError("지원하지 않는 상태")
        error = body.get("error")
        code = body.get("code")
        if error is not None and not isinstance(error, str):
            raise ValueError("error는 문자열이어야 함")
        if code is not None and not isinstance(code, str):
            raise ValueError("code는 문자열이어야 함")
        return {
            "commandName": command_name,
            "requestId": request_id,
            "status": status,
            "error": error,
            "code": code,
        }
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise CommandError(
            "INVALID_RESULT", "명령 결과 메시지를 해석할 수 없습니다."
        ) from exc


# --- 검증 ---


def require_command_name(command_name: str) -> None:
    if command_name not in COMMAND_NAMES:
        raise ValueError(f"지원하지 않는 명령: {command_name}")


def require_device_id(device_id: str) -> None:
    if (
        not isinstance(device_id, str)
        or not device_id
        or len(device_id) > 100
        or any(ch.isspace() or ord(ch) < 32 for ch in device_id)
    ):
        raise ValueError("deviceId 형식이 올바르지 않습니다.")


def require_request_id(value: str) -> None:
    """서버 ``CommandResultMessage`` 와 같은 규칙으로만 봅니다.

    UUID 를 강제하지 않습니다. 서버 DTO 가 ``@Size(max = 100) String`` 이라
    UUID 가 아닌 값도 유효하고, 로봇의 몫은 받은 값을 그대로 되돌리는
    것이지 서버 식별자의 형식을 정하는 것이 아닙니다. 여기서 UUID 를
    요구하면 서버가 형식을 바꾼 날 로봇이 멀쩡한 명령을 통째로 버립니다.

    귀가 명령은 ``eventId == requestId`` 라는 별도 제약이 있어 그쪽에서
    따로 UUID 를 확인합니다.
    """
    if (
        not isinstance(value, str)
        or not value
        or len(value) > MAX_REQUEST_ID_LENGTH
        or any(ch.isspace() or ord(ch) < 32 for ch in value)
    ):
        raise ValueError("requestId 형식이 올바르지 않습니다.")


def require_uuid(value: str, name: str) -> None:
    if not isinstance(value, str):
        raise ValueError(f"{name}는 UUID 문자열이어야 합니다.")
    try:
        UUID(value)
    except (ValueError, AttributeError) as exc:
        raise ValueError(f"{name}는 UUID 문자열이어야 합니다.") from exc


def validate_pose(pose: MapPose, request_id: Optional[str]) -> None:
    if not all(math.isfinite(value) for value in (pose.x, pose.y, pose.yaw)):
        raise CommandError(
            "INVALID_POSE", "좌표는 유한한 숫자여야 합니다.", request_id
        )
    if not -math.pi <= pose.yaw <= math.pi:
        raise CommandError(
            "INVALID_POSE", "yaw는 -pi~pi 범위여야 합니다.", request_id
        )


def pose_field(body: dict, prefix: str, request_id: Optional[str]) -> MapPose:
    """``x``/``y``/``yaw`` 또는 ``returnX``/``returnY``/``returnYaw`` 를 읽는다.

    지도 원점(0, 0, 0)도 정상 좌표입니다. 로봇 파라미터 쪽 규약("전부 0
    이면 미설정")을 여기로 옮기면 안 됩니다 — 서버는 미설정을 NULL 로
    두고 좌표 없는 위치로는 명령을 아예 발행하지 않습니다.
    """
    values = []
    for name in ("x", "y", "yaw"):
        field_name = f"{prefix}{name[0].upper()}{name[1:]}" if prefix else name
        value = body.get(field_name)
        if (
            not isinstance(value, (int, float))
            or isinstance(value, bool)
            or not math.isfinite(value)
        ):
            raise CommandError(
                "INVALID_POSE",
                f"{field_name}는 유한한 숫자여야 합니다.",
                request_id,
            )
        values.append(float(value))
    pose = MapPose(*values)
    validate_pose(pose, request_id)
    return pose


def best_effort_request_id(payload: Union[str, bytes]) -> Optional[str]:
    """거절을 회신하려면 requestId 라도 건져야 합니다."""
    try:
        if isinstance(payload, bytes):
            payload = payload.decode("utf-8")
        body = json.loads(payload)
        value = body.get("requestId") if isinstance(body, dict) else None
        require_request_id(value)
        return value
    except (
        AttributeError,
        TypeError,
        ValueError,
        UnicodeDecodeError,
        json.JSONDecodeError,
    ):
        return None
