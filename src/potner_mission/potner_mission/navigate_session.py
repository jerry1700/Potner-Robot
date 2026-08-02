"""서버 이동 명령의 순수 상태 머신.

ROS/Nav2와 분리해 중복 수신, 동시 명령, 도킹 실패, 스테이션 파킹을
로봇 없이 검증한다.

세 가지를 여기서 책임진다.

1. **중복 수신.** QoS 1 재전송에는 회신하지 않는다. 자세한 이유는
   :class:`~potner_mission.command_session.DecisionKind` 주석에 있다.
2. **동시 명령.** 다른 requestId 가 수행 중에 오면 BUSY 로 거절한다.
3. **스테이션 파킹.** 급수 스테이션에 도착한 뒤 그 자리에 서 있는
   동안을 기억한다. 마중 명령을 막는 근거다.
"""

from collections import OrderedDict
from dataclasses import dataclass
from enum import Enum, auto
from typing import Optional, Tuple

from potner_bridge.command_result import NAVIGATE, MapPose
from potner_bridge.navigate_contract import WATER_STATION, NavigateCommand
from potner_mission.command_session import (
    CommandDecision,
    DecisionKind,
    MissionResult,
)


class NavigateStage(Enum):
    NAVIGATING = auto()
    DOCKING = auto()


@dataclass
class NavigateSession:
    request_id: str
    destination: str
    pose: MapPose
    stage: NavigateStage
    started_at: float


class NavigateSessionController:
    """동시에 하나인 로봇의 이동 명령을 관리한다."""

    def __init__(self, result_cache_size: int = 128):
        self.active: Optional[NavigateSession] = None

        #: 마지막으로 도착해서 그대로 서 있는 목적지 이름. 출발하면 None.
        self.parked_at: Optional[str] = None

        self._results = OrderedDict()
        self._result_cache_size = result_cache_size

    # --- 명령 수락 ---

    def accept(
        self, command: NavigateCommand, now: float, robot_idle: bool
    ) -> CommandDecision:
        replay = self._results.get(command.request_id)
        if replay is not None:
            # 결과가 유실됐을 수 있으니 다시 보낸다. 서버는 이미 반영한
            # 명령이면 ALREADY_COMPLETED 로 흘리므로 상태가 흔들리지 않는다.
            return CommandDecision(DecisionKind.REPLAY, replay)
        if self.active is not None and self.active.request_id == command.request_id:
            return CommandDecision(DecisionKind.DUPLICATE_PENDING)
        if self.active is not None or not robot_idle:
            return CommandDecision(
                DecisionKind.BUSY,
                MissionResult(
                    NAVIGATE,
                    command.request_id,
                    "BUSY",
                    "다른 임무를 수행 중입니다.",
                    "ROBOT_BUSY",
                ),
            )

        self.active = NavigateSession(
            request_id=command.request_id,
            destination=command.destination,
            pose=command.pose,
            stage=NavigateStage.NAVIGATING,
            started_at=now,
        )
        # 출발하는 순간 파킹이 풀린다. 도착해야 다시 잡힌다.
        self.parked_at = None
        return CommandDecision(DecisionKind.ACCEPTED)

    # --- 진행 ---

    def begin_docking(self) -> None:
        session = self._require_active()
        session.stage = NavigateStage.DOCKING

    def reached(self) -> MissionResult:
        """도착(도킹까지 완료). ``OK`` 는 출발이 아니라 이 시점을 뜻한다."""
        session = self._require_active()
        self.parked_at = session.destination
        self.active = None
        result = MissionResult(NAVIGATE, session.request_id, "OK")
        self.remember(result)
        return result

    def fail_current(self, error: str, code: str) -> Optional[MissionResult]:
        session = self.active
        if session is None:
            return None
        self.active = None
        # 어디에 멈췄는지 알 수 없다. 스테이션에 있다고 가정하면 마중을
        # 영영 막는다.
        self.parked_at = None
        result = MissionResult(NAVIGATE, session.request_id, "ERROR", error, code)
        self.remember(result)
        return result

    # --- 다른 임무와의 관계 ---

    @property
    def blocks_arrival(self) -> bool:
        """마중 명령을 받아서는 안 되는 상태인지.

        이동 중이거나, 급수 스테이션에 대어 놓은 동안이다. 스테이션에 선
        로봇은 서버가 라즈베리에 급수·촬영·송풍을 시키기를 기다리는
        중인데, 젯슨은 그 작업이 끝났는지 알 방법이 없다 — 서버가 다음
        이동 명령을 보내는 것이 유일한 신호다. 그 사이에 마중을 나가면
        물이 바닥으로 쏟아진다.

        서버는 이걸 막지 못한다. ``RobotBusyGuard`` 는 ``device_command``
        만 보고 마중 세션을 모르고, 마중 쪽도 급수 명령을 모른다.
        """
        return self.active is not None or self.parked_at == WATER_STATION

    def busy_reason(self) -> Tuple[Optional[str], Optional[str]]:
        """:attr:`blocks_arrival` 일 때 거절 사유 ``(error, code)``."""
        if self.active is not None:
            return "서버 이동 명령을 수행 중입니다.", "NAVIGATING"
        if self.parked_at == WATER_STATION:
            return "급수 스테이션에서 작업 중입니다.", "SERVICING_AT_STATION"
        return None, None

    def left_station(self) -> None:
        """이동 명령 밖의 이유로 로봇이 자리를 떴을 때 파킹을 푼다."""
        self.parked_at = None

    # --- 보조 ---

    def remember(self, result: MissionResult) -> None:
        self._results[result.request_id] = result
        self._results.move_to_end(result.request_id)
        while len(self._results) > self._result_cache_size:
            self._results.popitem(last=False)

    def _require_active(self) -> NavigateSession:
        if self.active is None:
            raise RuntimeError("진행 중인 이동 명령이 없습니다.")
        return self.active
