"""이동 명령(``command/navigate``) 계약.

서버가 모든 판단을 하고 로봇은 받은 명령만 수행합니다. 자동 급수·자동
말리기·자동 촬영·햇빛 이동 네 기능이 전부 이 명령으로 시작합니다.

ROS에 의존하지 않으므로 브로커 없이 CI에서 검증됩니다. 결과 형식은
:mod:`potner_bridge.command_result` 를 씁니다 — 귀가 명령과 같습니다.

★ **좌표는 반드시 페이로드 값을 쓰세요.** 로봇 설정에 목적지 좌표를 두면
  출처가 두 곳이 되어, 지도를 다시 그렸을 때 한쪽만 갱신되고 엉뚱한 곳으로
  갑니다. 서버의 ``robot_location`` 이 유일한 출처입니다
  (DEVICE-MQTT.md 17절).
"""

import json
from dataclasses import asdict, dataclass
from typing import Optional, Union

from potner_bridge.command_result import (
    NAVIGATE,
    CommandError,
    MapPose,
    best_effort_request_id,
    pose_field,
    require_request_id,
    result_message,
    result_topic,
    validate_pose,
)

# 서버 RobotLocationType 과 1:1. 그 외 값은 서버가 보내지 않습니다.
WATER_STATION = "WATER_STATION"
HOME = "HOME"
SUNLIGHT = "SUNLIGHT"
GREETING = "GREETING"

DESTINATIONS = frozenset({WATER_STATION, HOME, SUNLIGHT, GREETING})

# 정밀 도킹이 필요한 목적지 -> ArUco 마커 ID (src/potner_docking/markers/).
#
# 마커는 2종만 씁니다.
#
#   1  대기 자리 — 붙여는 두지만 아직 도킹하지 않습니다 (아래 참고)
#   2  스테이션  — 급수·송풍·촬영이 한 대에 모여 있습니다
#
# 물리 장치가 있는 곳은 스테이션뿐입니다
# (``RobotLocationType.isPhysicalStation()``). 펌프 노즐과 카메라를 cm
# 단위로 맞춰야 해서 여기만 마커를 봅니다. 서버도 자동 급수·말리기·촬영
# 세 체인을 모두 ``WATER_STATION`` 하나로 보냅니다 — 송풍 전용 위치는
# 서버 ``RobotLocationType`` 에 아예 없습니다.
#
# 햇빛 자리는 SLAM 지도 위의 좌표일 뿐이라 Nav2 의 도착 오차(수십 cm)로
# 충분합니다.
#
# ★ 대기 자리(HOME)에 마커를 붙여도 여기 넣지 않았습니다. 모든 자동
#   체인의 마지막 단계가 ``NAVIGATE(HOME)`` 인데, 서버는 "로봇이 지금
#   어디 있나" 를 **마지막 OK 이동**으로 추적합니다
#   (``SunlightRelocationScheduler``). 복귀 도킹이 실패하면 급수는 이미
#   끝났는데 이력에 ERROR 가 남고 서버의 위치 추적이 어긋납니다.
#   되돌아가는 이동에 cm 정밀도가 필요할 이유가 없으므로 Nav2 로만
#   갑니다. 충전 접점이 생기면 ``HOME: 1`` 한 줄을 넣으면 됩니다.
DOCKING_MARKERS = {WATER_STATION: 2}


@dataclass(frozen=True)
class NavigateCommand:
    request_id: str
    destination: str
    pose: MapPose
    command_name: str = NAVIGATE

    @property
    def marker_id(self) -> Optional[int]:
        """도킹할 마커 번호. 도킹이 필요 없는 목적지면 ``None``."""
        return DOCKING_MARKERS.get(self.destination)


def navigate_result_topic(device_id: str) -> str:
    return result_topic(device_id, NAVIGATE)


def navigate_result_message(
    device_id: str,
    request_id: str,
    status: str,
    *,
    error: Optional[str] = None,
    code: Optional[str] = None,
    message_id: Optional[str] = None,
) -> str:
    return result_message(
        device_id,
        request_id,
        status,
        error=error,
        code=code,
        message_id=message_id,
    )


def parse_navigate_command(payload: Union[str, bytes]) -> NavigateCommand:
    """서버 이동 명령을 검증해 주행 코드가 바로 쓸 수 있는 값으로 바꾼다."""
    request_id = best_effort_request_id(payload)
    try:
        if isinstance(payload, bytes):
            payload = payload.decode("utf-8")
        body = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError) as exc:
        raise CommandError(
            "INVALID_JSON", "이동 명령 JSON을 해석할 수 없습니다.", request_id
        ) from exc

    if not isinstance(body, dict):
        raise CommandError(
            "INVALID_PAYLOAD", "명령 본문은 JSON 객체여야 합니다.", request_id
        )

    request_id = _request_id_field(body)
    destination = body.get("destination")
    if destination not in DESTINATIONS:
        # 서버가 목적지를 늘렸거나 오타입니다. 어느 쪽이든 갈 곳을 모르므로
        # 출발하지 않고 거절합니다 — 서버가 제한시간까지 기다리지 않게.
        raise CommandError(
            "INVALID_DESTINATION",
            f"모르는 목적지입니다: {destination!r}",
            request_id,
        )

    return NavigateCommand(
        request_id=request_id,
        destination=destination,
        pose=pose_field(body, "", request_id),
    )


def navigate_command_json(command: NavigateCommand) -> str:
    """검증된 명령을 ROS String으로 전달할 때 쓰는 내부 JSON."""
    body = asdict(command)
    body["commandName"] = body.pop("command_name")
    body["pose"] = asdict(command.pose)
    return json.dumps(body, ensure_ascii=False, separators=(",", ":"))


def parse_internal_navigate_command(payload: Union[str, bytes]) -> NavigateCommand:
    """MQTT bridge가 만든 내부 JSON을 mission_manager에서 복원한다."""
    try:
        if isinstance(payload, bytes):
            payload = payload.decode("utf-8")
        body = json.loads(payload)
        if body["commandName"] != NAVIGATE:
            raise ValueError(f"지원하지 않는 명령: {body['commandName']}")
        request_id = body["request_id"]
        destination = body["destination"]
        require_request_id(request_id)
        if destination not in DESTINATIONS:
            raise ValueError(f"모르는 목적지: {destination}")
        pose = MapPose(**body["pose"])
        validate_pose(pose, request_id)
        return NavigateCommand(
            request_id=request_id, destination=destination, pose=pose
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
        # requestId 를 못 읽으면 회신할 방법이 없습니다. 서버는 이 명령을
        # 제한시간까지 기다렸다가 TIMED_OUT 으로 끊습니다.
        raise CommandError("INVALID_IDENTIFIER", str(exc), None) from exc
    return value
