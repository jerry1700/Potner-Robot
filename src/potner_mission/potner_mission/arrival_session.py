"""귀가 마중 임무의 순수 상태 머신.

ROS/Nav2와 분리해 중복 명령, 다른 방문의 취소, 제한시간을 로봇 없이 검증한다.
"""

from collections import OrderedDict
from dataclasses import dataclass
from enum import Enum, auto
from typing import Optional

from potner_bridge.arrival_contract import (
    WELCOME_CANCEL,
    WELCOME_START,
    MapPose,
    WelcomeCancelCommand,
    WelcomeStartCommand,
)

# 이동 명령과 판정 종류가 같다. 재수출해 두어 기존 import 경로가 유지된다.
from potner_mission.command_session import (  # noqa: F401
    CommandDecision,
    DecisionKind,
    MissionResult,
)

DEFAULT_BUSY_ERROR = "다른 임무를 수행 중입니다."
DEFAULT_BUSY_CODE = "ROBOT_BUSY"


class ArrivalStage(Enum):
    NAVIGATING_GREETING = auto()
    WAITING_AT_GREETING = auto()
    NAVIGATING_HOME = auto()


@dataclass
class ArrivalSession:
    visit_id: str
    home: MapPose
    stage: ArrivalStage
    started_at: float
    start_request_id: Optional[str] = None
    greeting: Optional[MapPose] = None
    wait_seconds: Optional[int] = None
    total_timeout_seconds: Optional[int] = None
    greeting_reached_at: Optional[float] = None
    cancel_request_id: Optional[str] = None


class ArrivalSessionController:
    """동시에 하나인 로봇의 귀가 임무를 관리한다."""

    def __init__(self, result_cache_size: int = 128):
        self.active: Optional[ArrivalSession] = None
        self._results = OrderedDict()
        self._result_cache_size = result_cache_size

    def accept_start(
        self,
        command: WelcomeStartCommand,
        now: float,
        robot_idle: bool,
        *,
        busy_error: Optional[str] = None,
        busy_code: Optional[str] = None,
    ) -> CommandDecision:
        """마중을 시작할지 정한다.

        ``busy_error``/``busy_code`` 는 호출자가 아는 거절 사유다. 로봇이
        급수 스테이션에 대어 놓은 상태처럼, 여기서는 보이지 않고
        mission_manager 만 아는 이유를 서버에 그대로 전달하기 위한 것이다.
        """
        replay = self._results.get(command.request_id)
        if replay is not None:
            return CommandDecision(DecisionKind.REPLAY, replay)
        if (
            self.active is not None
            and self.active.start_request_id == command.request_id
        ):
            return CommandDecision(DecisionKind.DUPLICATE_PENDING)
        if self.active is not None or not robot_idle:
            return CommandDecision(
                DecisionKind.BUSY,
                MissionResult(
                    WELCOME_START,
                    command.request_id,
                    "BUSY",
                    busy_error or DEFAULT_BUSY_ERROR,
                    busy_code or DEFAULT_BUSY_CODE,
                ),
            )

        self.active = ArrivalSession(
            visit_id=command.visit_id,
            home=command.home,
            stage=ArrivalStage.NAVIGATING_GREETING,
            started_at=now,
            start_request_id=command.request_id,
            greeting=command.greeting,
            wait_seconds=command.wait_seconds,
            total_timeout_seconds=command.total_timeout_seconds,
        )
        return CommandDecision(DecisionKind.ACCEPTED)

    def accept_cancel(
        self,
        command: WelcomeCancelCommand,
        now: float,
        robot_idle: bool,
        *,
        busy_error: Optional[str] = None,
        busy_code: Optional[str] = None,
    ) -> CommandDecision:
        replay = self._results.get(command.request_id)
        if replay is not None:
            return CommandDecision(DecisionKind.REPLAY, replay)
        if (
            self.active is not None
            and self.active.cancel_request_id == command.request_id
        ):
            return CommandDecision(DecisionKind.DUPLICATE_PENDING)
        if (
            self.active is not None
            and self.active.cancel_request_id is not None
        ):
            return self._busy_cancel(
                command, "이미 HOME 복귀 명령을 수행 중입니다."
            )
        if self.active is not None and self.active.visit_id != command.visit_id:
            return self._busy_cancel(command, "다른 방문의 귀가 임무를 수행 중입니다.")
        if self.active is None and not robot_idle:
            # 귀가 세션 밖의 임무가 로봇을 붙들고 있다. 그 사유는 여기서
            # 보이지 않으므로 호출자가 준 것을 그대로 쓴다.
            return self._busy_cancel(
                command, busy_error or DEFAULT_BUSY_ERROR, busy_code
            )

        if self.active is None:
            self.active = ArrivalSession(
                visit_id=command.visit_id,
                home=command.home,
                stage=ArrivalStage.NAVIGATING_HOME,
                started_at=now,
                cancel_request_id=command.request_id,
            )
        else:
            self.active.home = command.home
            self.active.cancel_request_id = command.request_id
            self.active.stage = ArrivalStage.NAVIGATING_HOME
        return CommandDecision(DecisionKind.ACCEPTED)

    def greeting_reached(self, now: float) -> MissionResult:
        session = self._require_stage(ArrivalStage.NAVIGATING_GREETING)
        session.stage = ArrivalStage.WAITING_AT_GREETING
        session.greeting_reached_at = now
        result = MissionResult(
            WELCOME_START, session.start_request_id, "OK"
        )
        self.remember(result)
        return result

    def waiting_expired(self, now: float) -> bool:
        session = self.active
        if session is None or session.stage is not ArrivalStage.WAITING_AT_GREETING:
            return False
        return now >= session.greeting_reached_at + session.wait_seconds

    def total_timeout_expired(self, now: float) -> bool:
        session = self.active
        if session is None or session.total_timeout_seconds is None:
            return False
        return now >= session.started_at + session.total_timeout_seconds

    def begin_return_home(self) -> None:
        session = self.active
        if session is None:
            raise RuntimeError("활성 귀가 임무가 없습니다.")
        session.stage = ArrivalStage.NAVIGATING_HOME

    def reset_total_timeout(self, now: float, seconds: int) -> None:
        session = self.active
        if session is None:
            raise RuntimeError("활성 귀가 임무가 없습니다.")
        if seconds <= 0:
            raise ValueError("제한시간은 양수여야 합니다.")
        session.started_at = now
        session.total_timeout_seconds = seconds

    def home_reached(self) -> Optional[MissionResult]:
        session = self._require_stage(ArrivalStage.NAVIGATING_HOME)
        result = None
        if session.cancel_request_id is not None:
            result = MissionResult(
                WELCOME_CANCEL, session.cancel_request_id, "OK"
            )
            self.remember(result)
        self.active = None
        return result

    def fail_current(self, error: str, code: str) -> Optional[MissionResult]:
        session = self.active
        if session is None:
            return None
        result = None
        if (
            session.stage is ArrivalStage.NAVIGATING_GREETING
            and session.start_request_id is not None
        ):
            result = MissionResult(
                WELCOME_START, session.start_request_id, "ERROR", error, code
            )
            session.stage = ArrivalStage.NAVIGATING_HOME
        elif session.cancel_request_id is not None:
            result = MissionResult(
                WELCOME_CANCEL, session.cancel_request_id, "ERROR", error, code
            )
            self.active = None
        else:
            self.active = None
        if result is not None:
            self.remember(result)
        return result

    def remember(self, result: MissionResult) -> None:
        self._results[result.request_id] = result
        self._results.move_to_end(result.request_id)
        while len(self._results) > self._result_cache_size:
            self._results.popitem(last=False)

    def _busy_cancel(
        self,
        command: WelcomeCancelCommand,
        error: str,
        code: Optional[str] = None,
    ) -> CommandDecision:
        return CommandDecision(
            DecisionKind.BUSY,
            MissionResult(
                WELCOME_CANCEL,
                command.request_id,
                "BUSY",
                error,
                code or DEFAULT_BUSY_CODE,
            ),
        )

    def _require_stage(self, stage: ArrivalStage) -> ArrivalSession:
        if self.active is None or self.active.stage is not stage:
            raise RuntimeError(f"예상하지 않은 귀가 단계: {stage.name}")
        return self.active
