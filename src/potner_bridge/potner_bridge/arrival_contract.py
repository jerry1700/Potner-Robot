"""귀가 마중 MQTT 명령과 결과 메시지 계약.

이 모듈은 ROS에 의존하지 않는다. 브로커에서 받은 값을 움직임 코드에 넘기기 전에
검증하고, 서버가 대조할 수 있는 결과 메시지를 만드는 단일 기준으로 사용한다.

결과 토픽·결과 JSON·ROS 내부 봉투는 이동 명령과 형식이 같아서
:mod:`potner_bridge.command_result` 에 모아 두었다. 여기서는 귀가 명령에만
있는 제약(``eventId == requestId``, 목적지 고정)을 얹는다.
"""

import json
from dataclasses import asdict, dataclass
from typing import Optional, Union
from uuid import UUID

from potner_bridge.command_result import (
    RESULT_STATUSES,
    WELCOME_CANCEL,
    WELCOME_START,
    CommandError,
    MapPose,
    pose_field,
    require_request_id,
    require_uuid,
    result_envelope,
    result_message,
    result_topic,
    validate_pose,
)
from potner_bridge.command_result import parse_result_envelope  # noqa: F401  재수출

# 귀가 코드가 오래 쓴 이름이다. 같은 예외 클래스라 어느 쪽으로 잡아도 된다.
ArrivalCommandError = CommandError

ARRIVAL_COMMANDS = frozenset({WELCOME_START, WELCOME_CANCEL})


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
    _require_arrival_command(command_name)
    return result_topic(device_id, command_name)


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
    require_uuid(request_id, "requestId")
    return result_message(
        device_id,
        request_id,
        status,
        error=error,
        code=code,
        message_id=message_id,
    )


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
        greeting = pose_field(body, "", request_id)
        home = pose_field(body, "return", request_id)
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
            home=pose_field(body, "return", request_id),
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
        validate_pose(home, request_id)
        require_uuid(request_id, "requestId")
        require_uuid(event_id, "eventId")
        if event_id != request_id:
            raise ValueError("eventId와 requestId 불일치")
        require_uuid(visit_id, "visitId")
        if command_name == WELCOME_CANCEL:
            return WelcomeCancelCommand(event_id, visit_id, request_id, home)
        if command_name == WELCOME_START:
            greeting = MapPose(**body["greeting"])
            validate_pose(greeting, request_id)
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


def _require_arrival_command(command_name: str) -> None:
    if command_name not in ARRIVAL_COMMANDS:
        raise ValueError(f"지원하지 않는 귀가 명령: {command_name}")


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
        require_uuid(value, name)
    except ValueError as exc:
        raise ArrivalCommandError(
            "INVALID_IDENTIFIER", str(exc), request_id
        ) from exc
    return value


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


__all__ = [
    "ARRIVAL_COMMANDS",
    "RESULT_STATUSES",
    "WELCOME_CANCEL",
    "WELCOME_START",
    "ArrivalCommand",
    "ArrivalCommandError",
    "MapPose",
    "WelcomeCancelCommand",
    "WelcomeStartCommand",
    "arrival_command_json",
    "arrival_result_message",
    "arrival_result_topic",
    "parse_arrival_command",
    "parse_internal_arrival_command",
    "parse_result_envelope",
    "require_request_id",
    "result_envelope",
]
