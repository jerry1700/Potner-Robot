"""귀가 마중 MQTT 명령과 결과 메시지 계약.

이 모듈은 ROS에 의존하지 않는다. 브로커에서 받은 값을 움직임 코드에 넘기기 전에
검증하고, 서버가 대조할 수 있는 결과 메시지를 만드는 단일 기준으로 사용한다.
"""

import json
import math
from dataclasses import asdict, dataclass
from typing import Optional, Union
from uuid import UUID, uuid4

WELCOME_START = "welcome_start"
WELCOME_CANCEL = "welcome_cancel"
RESULT_STATUSES = {"OK", "ERROR", "BUSY"}


class ArrivalCommandError(ValueError):
    """안전하게 실행할 수 없는 귀가 명령."""

    def __init__(self, code: str, message: str, request_id: Optional[str] = None):
        super().__init__(message)
        self.code = code
        self.request_id = request_id


@dataclass(frozen=True)
class MapPose:
    x: float
    y: float
    yaw: float


@dataclass(frozen=True)
class WelcomeStartCommand:
    event_id: str
    visit_id: str
    request_id: str
    greeting: MapPose
    home: MapPose
    wait_seconds: int
    total_timeout_seconds: int
    command_name: str = WELCOME_START


@dataclass(frozen=True)
class WelcomeCancelCommand:
    event_id: str
    visit_id: str
    request_id: str
    home: MapPose
    command_name: str = WELCOME_CANCEL


ArrivalCommand = Union[WelcomeStartCommand, WelcomeCancelCommand]


def arrival_result_topic(device_id: str, command_name: str) -> str:
    _require_device_id(device_id)
    if command_name not in {WELCOME_START, WELCOME_CANCEL}:
        raise ValueError(f"지원하지 않는 귀가 명령: {command_name}")
    return f"potner/device/{device_id}/result/{command_name}"


def arrival_result_message(
    device_id: str,
    request_id: str,
    status: str,
    *,
    error: Optional[str] = None,
    code: Optional[str] = None,
    message_id: Optional[str] = None,
) -> str:
    """서버 ``CommandResultMessage``와 호환되는 JSON을 만든다."""
    _require_device_id(device_id)
    _require_uuid(request_id, "requestId")
    if status not in RESULT_STATUSES:
        raise ValueError(f"지원하지 않는 결과 상태: {status}")
    if status == "OK" and (error is not None or code is not None):
        raise ValueError("OK 결과에는 error/code를 넣을 수 없습니다.")

    resolved_message_id = message_id or str(uuid4())
    _require_uuid(resolved_message_id, "messageId")
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


def parse_arrival_command(
    command_name: str, payload: Union[str, bytes]
) -> ArrivalCommand:
    """서버 명령을 검증하고 움직임 코드가 바로 쓸 수 있는 값으로 바꾼다."""
    request_id = _best_effort_request_id(payload)
    try:
        if isinstance(payload, bytes):
            payload = payload.decode("utf-8")
        body = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError) as exc:
        raise ArrivalCommandError(
            "INVALID_JSON", "명령 JSON을 해석할 수 없습니다.", request_id
        ) from exc

    if not isinstance(body, dict):
        raise ArrivalCommandError(
            "INVALID_PAYLOAD", "명령 본문은 JSON 객체여야 합니다.", request_id
        )

    request_id = _uuid_field(body, "requestId")
    event_id = _uuid_field(body, "eventId", request_id)
    visit_id = _uuid_field(body, "visitId", request_id)
    if event_id != request_id:
        raise ArrivalCommandError(
            "MISMATCHED_REQUEST_ID",
            "eventId와 requestId가 같아야 합니다.",
            request_id,
        )

    if command_name == WELCOME_START:
        if body.get("destination") != "GREETING":
            raise ArrivalCommandError(
                "INVALID_DESTINATION",
                "welcome_start 목적지는 GREETING이어야 합니다.",
                request_id,
            )
        _require_home(body, request_id)
        greeting = _pose(body, "", request_id)
        home = _pose(body, "return", request_id)
        wait_seconds = _positive_int(body, "waitSeconds", request_id)
        total_timeout_seconds = _positive_int(
            body, "totalTimeoutSeconds", request_id
        )
        if total_timeout_seconds <= wait_seconds:
            raise ArrivalCommandError(
                "INVALID_TIMEOUT",
                "totalTimeoutSeconds는 waitSeconds보다 커야 합니다.",
                request_id,
            )
        return WelcomeStartCommand(
            event_id=event_id,
            visit_id=visit_id,
            request_id=request_id,
            greeting=greeting,
            home=home,
            wait_seconds=wait_seconds,
            total_timeout_seconds=total_timeout_seconds,
        )

    if command_name == WELCOME_CANCEL:
        _require_home(body, request_id)
        return WelcomeCancelCommand(
            event_id=event_id,
            visit_id=visit_id,
            request_id=request_id,
            home=_pose(body, "return", request_id),
        )

    raise ArrivalCommandError(
        "UNSUPPORTED_COMMAND",
        f"지원하지 않는 귀가 명령입니다: {command_name}",
        request_id,
    )


def arrival_command_json(command: ArrivalCommand) -> str:
    """검증된 명령을 ROS String으로 전달할 때 쓰는 내부 JSON."""
    body = asdict(command)
    body["commandName"] = body.pop("command_name")
    if isinstance(command, WelcomeStartCommand):
        body["greeting"] = asdict(command.greeting)
        body["home"] = asdict(command.home)
    else:
        body["home"] = asdict(command.home)
    return json.dumps(body, ensure_ascii=False, separators=(",", ":"))


def parse_internal_arrival_command(payload: Union[str, bytes]) -> ArrivalCommand:
    """MQTT bridge가 만든 내부 JSON을 mission_manager에서 복원한다."""
    try:
        if isinstance(payload, bytes):
            payload = payload.decode("utf-8")
        body = json.loads(payload)
        command_name = body["commandName"]
        event_id = body["event_id"]
        visit_id = body["visit_id"]
        request_id = body["request_id"]
        home = MapPose(**body["home"])
        _validate_pose(home, request_id)
        _require_uuid(request_id, "requestId")
        _require_uuid(event_id, "eventId")
        if event_id != request_id:
            raise ValueError("eventId와 requestId 불일치")
        _require_uuid(visit_id, "visitId")
        if command_name == WELCOME_CANCEL:
            return WelcomeCancelCommand(event_id, visit_id, request_id, home)
        if command_name == WELCOME_START:
            greeting = MapPose(**body["greeting"])
            _validate_pose(greeting, request_id)
            wait_seconds = body["wait_seconds"]
            total_timeout_seconds = body["total_timeout_seconds"]
            if (
                not isinstance(wait_seconds, int)
                or isinstance(wait_seconds, bool)
                or wait_seconds <= 0
                or not isinstance(total_timeout_seconds, int)
                or isinstance(total_timeout_seconds, bool)
                or total_timeout_seconds <= wait_seconds
            ):
                raise ValueError("대기/전체 제한시간 오류")
            return WelcomeStartCommand(
                event_id,
                visit_id,
                request_id,
                greeting,
                home,
                wait_seconds,
                total_timeout_seconds,
            )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ArrivalCommandError(
            "INVALID_INTERNAL_COMMAND", "검증된 내부 명령이 손상되었습니다."
        ) from exc
    raise ArrivalCommandError(
        "UNSUPPORTED_COMMAND", f"지원하지 않는 귀가 명령입니다: {command_name}"
    )


def result_envelope(
    command_name: str,
    request_id: str,
    status: str,
    *,
    error: Optional[str] = None,
    code: Optional[str] = None,
) -> str:
    """mission_manager에서 MQTT bridge로 보내는 ROS 내부 결과."""
    _require_uuid(request_id, "requestId")
    if command_name not in {WELCOME_START, WELCOME_CANCEL}:
        raise ValueError(f"지원하지 않는 귀가 명령: {command_name}")
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
        _require_uuid(request_id, "requestId")
        if command_name not in {WELCOME_START, WELCOME_CANCEL}:
            raise ValueError("지원하지 않는 명령")
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
        raise ArrivalCommandError(
            "INVALID_RESULT", "귀가 결과 메시지를 해석할 수 없습니다."
        ) from exc


def _best_effort_request_id(payload: Union[str, bytes]) -> Optional[str]:
    try:
        if isinstance(payload, bytes):
            payload = payload.decode("utf-8")
        body = json.loads(payload)
        value = body.get("requestId") if isinstance(body, dict) else None
        UUID(value)
        return value
    except (
        AttributeError,
        TypeError,
        ValueError,
        UnicodeDecodeError,
        json.JSONDecodeError,
    ):
        return None


def _uuid_field(body: dict, name: str, request_id: Optional[str] = None) -> str:
    value = body.get(name)
    try:
        _require_uuid(value, name)
    except ValueError as exc:
        raise ArrivalCommandError(
            "INVALID_IDENTIFIER", str(exc), request_id
        ) from exc
    return value


def _require_uuid(value: str, name: str) -> None:
    if not isinstance(value, str):
        raise ValueError(f"{name}는 UUID 문자열이어야 합니다.")
    try:
        UUID(value)
    except (ValueError, AttributeError) as exc:
        raise ValueError(f"{name}는 UUID 문자열이어야 합니다.") from exc


def _positive_int(body: dict, name: str, request_id: str) -> int:
    value = body.get(name)
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ArrivalCommandError(
            "INVALID_TIMEOUT", f"{name}는 양의 정수여야 합니다.", request_id
        )
    return value


def _require_home(body: dict, request_id: str) -> None:
    if body.get("returnDestination") != "HOME":
        raise ArrivalCommandError(
            "INVALID_DESTINATION",
            "복귀 목적지는 HOME이어야 합니다.",
            request_id,
        )


def _pose(body: dict, prefix: str, request_id: str) -> MapPose:
    values = []
    for name in ("x", "y", "yaw"):
        field_name = (
            f"{prefix}{name[0].upper()}{name[1:]}" if prefix else name
        )
        value = body.get(field_name)
        if (
            not isinstance(value, (int, float))
            or isinstance(value, bool)
            or not math.isfinite(value)
        ):
            raise ArrivalCommandError(
                "INVALID_POSE",
                f"{field_name}는 유한한 숫자여야 합니다.",
                request_id,
            )
        values.append(float(value))
    pose = MapPose(*values)
    _validate_pose(pose, request_id)
    return pose


def _validate_pose(pose: MapPose, request_id: Optional[str]) -> None:
    if not all(math.isfinite(value) for value in (pose.x, pose.y, pose.yaw)):
        raise ArrivalCommandError(
            "INVALID_POSE", "좌표는 유한한 숫자여야 합니다.", request_id
        )
    if not -math.pi <= pose.yaw <= math.pi:
        raise ArrivalCommandError(
            "INVALID_POSE", "yaw는 -pi~pi 범위여야 합니다.", request_id
        )


def _require_device_id(device_id: str) -> None:
    if (
        not isinstance(device_id, str)
        or not device_id
        or len(device_id) > 100
        or any(ch.isspace() or ord(ch) < 32 for ch in device_id)
    ):
        raise ValueError("deviceId 형식이 올바르지 않습니다.")
