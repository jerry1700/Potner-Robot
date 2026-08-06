"""수동 주행 명령(``command/drive``) 계약.

앱 방향 버튼 하나가 이 명령 하나를 만듭니다. 서버가 속도·지속시간을 전부
정해서 보내고, 로봇은 받은 값을 그대로 실행만 합니다(``potner.drive.*``
서버 설정이 유일한 속도 출처 — 로봇 쪽에 따로 속도를 두면 서버가 값을
바꿔도 로봇이 안 따라옵니다).

ROS에 의존하지 않으므로 브로커 없이 CI에서 검증됩니다.

★ navigate·귀가 명령과 달리 **회신이 없습니다.** drive는 ``device_command``
  이력을 거치지 않는 별도 통로라, 서버의 ``CommandResultTopicParser`` 는
  water/capture/fan/navigate 만 인식합니다. ``result/drive`` 를 보내도
  서버가 조용히 버리므로, 이 모듈은 :mod:`potner_bridge.command_result` 의
  ``COMMAND_NAMES``/``RESULT_STATUSES``/``result_topic``/``result_message``
  를 건드리지 않고 파싱에 필요한 것만 가져다 씁니다.
"""

import json
import math
from dataclasses import asdict, dataclass
from typing import Optional, Union

from potner_bridge.command_result import (
    CommandError,
    best_effort_request_id,
    require_request_id,
)

DRIVE = "drive"


@dataclass(frozen=True)
class DriveCommand:
    request_id: str
    linear_mps: float
    angular_rps: float
    duration_ms: int
    direction: str
    command_name: str = DRIVE


def parse_drive_command(payload: Union[str, bytes]) -> DriveCommand:
    """서버 주행 명령을 검증해 주행 코드가 바로 쓸 수 있는 값으로 바꾼다."""
    request_id = best_effort_request_id(payload)
    try:
        if isinstance(payload, bytes):
            payload = payload.decode("utf-8")
        body = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError) as exc:
        raise CommandError(
            "INVALID_JSON", "주행 명령 JSON을 해석할 수 없습니다.", request_id
        ) from exc

    if not isinstance(body, dict):
        raise CommandError(
            "INVALID_PAYLOAD", "명령 본문은 JSON 객체여야 합니다.", request_id
        )

    request_id = _request_id_field(body)
    return DriveCommand(
        request_id=request_id,
        linear_mps=_finite_number_field(body, "linearMps", request_id),
        angular_rps=_finite_number_field(body, "angularRps", request_id),
        duration_ms=_duration_field(body, request_id),
        direction=_direction_field(body, request_id),
    )


def hold_seconds(
    command: DriveCommand, turn_angle_rad: float, step_distance_m: float
) -> float:
    """명령을 유지할 시간(초).

    버튼 한 번이 늘 **같은 거리·같은 각도**를 움직여야 사람이 로봇 위치를
    가늠하며 조작할 수 있습니다. 그래서 서버가 준 ``durationMs`` 대신
    목표량을 채울 만큼의 시간을 계산합니다.

        제자리 회전   목표 각도 / 각속도
        직진·후진     목표 거리 / 선속도
        그 외         서버의 durationMs (정지, 곡선 주행)

    서버 기본값(0.12m/s, 600ms)을 그대로 쓰면 한 번에 7cm 밖에 못 가는데,
    가속에만 0.6초가 걸려 실측 이동 거리는 2~3cm 였습니다. 좌표 등록용
    조작에는 너무 짧습니다.

    ★ 회전 각도의 정확도는 ``wheel_separation`` 실측에 의존합니다. 이 값이
      틀리면 명령한 각속도와 실제 각속도가 어긋나 회전량이 비례해서
      틀어집니다. 오도메트리로 닫아도 같은 상수를 쓰므로 상쇄되지 않습니다.

    Args:
        turn_angle_rad: 좌/우 버튼 한 번에 돌 각도 (rad)
        step_distance_m: 전진/후진 버튼 한 번에 갈 거리 (m)
    """
    if command.linear_mps == 0.0 and command.angular_rps != 0.0:
        return abs(turn_angle_rad / command.angular_rps)
    if command.angular_rps == 0.0 and command.linear_mps != 0.0:
        return abs(step_distance_m / command.linear_mps)
    return command.duration_ms / 1000.0


def drive_command_json(command: DriveCommand) -> str:
    """검증된 명령을 ROS String으로 전달할 때 쓰는 내부 JSON."""
    body = asdict(command)
    body["commandName"] = body.pop("command_name")
    return json.dumps(body, ensure_ascii=False, separators=(",", ":"))


def parse_internal_drive_command(payload: Union[str, bytes]) -> DriveCommand:
    """MQTT bridge가 만든 내부 JSON을 실행 노드에서 복원한다."""
    try:
        if isinstance(payload, bytes):
            payload = payload.decode("utf-8")
        body = json.loads(payload)
        if body["commandName"] != DRIVE:
            raise ValueError(f"지원하지 않는 명령: {body['commandName']}")
        request_id = body["request_id"]
        require_request_id(request_id)
        linear_mps = float(body["linear_mps"])
        angular_rps = float(body["angular_rps"])
        duration_ms = int(body["duration_ms"])
        direction = str(body["direction"])
        if not math.isfinite(linear_mps) or not math.isfinite(angular_rps):
            raise ValueError("속도는 유한한 숫자여야 합니다.")
        if duration_ms < 0:
            raise ValueError("durationMs는 0 이상이어야 합니다.")
        return DriveCommand(
            request_id=request_id,
            linear_mps=linear_mps,
            angular_rps=angular_rps,
            duration_ms=duration_ms,
            direction=direction,
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise CommandError(
            "INVALID_INTERNAL_COMMAND", "검증된 내부 명령이 손상되었습니다."
        ) from exc


def _request_id_field(body: dict) -> str:
    value = body.get("requestId")
    try:
        require_request_id(value)
    except ValueError as exc:
        # requestId 를 못 읽으면 회신할 방법이 없습니다(애초에 drive는
        # 회신이 없지만, 로그에서라도 어떤 요청인지 추적할 수 있어야 합니다).
        raise CommandError("INVALID_IDENTIFIER", str(exc), None) from exc
    return value


def _finite_number_field(
    body: dict, name: str, request_id: Optional[str]
) -> float:
    value = body.get(name)
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not math.isfinite(value)
    ):
        raise CommandError(
            "INVALID_VELOCITY", f"{name}는 유한한 숫자여야 합니다.", request_id
        )
    return float(value)


def _duration_field(body: dict, request_id: Optional[str]) -> int:
    value = body.get("durationMs")
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not math.isfinite(value)
        or value < 0
    ):
        raise CommandError(
            "INVALID_DURATION", "durationMs는 0 이상의 숫자여야 합니다.", request_id
        )
    return int(value)


def _direction_field(body: dict, request_id: Optional[str]) -> str:
    """로그용 문자열일 뿐이라 알려진 다섯 값으로 제한하지 않습니다.

    실제로 로봇을 움직이는 값은 linearMps/angularRps 뿐입니다(서버
    DriveCommandPayload 문서 참고). 여기서 값 목록을 엄격히 검증하면,
    서버가 새 방향 라벨을 추가했을 때 정상적인 주행 명령까지 거절하게
    됩니다.
    """
    value = body.get("direction")
    if not isinstance(value, str) or not value:
        raise CommandError(
            "INVALID_DIRECTION", "direction은 빈 값이 아닌 문자열이어야 합니다.",
            request_id,
        )
    return value
