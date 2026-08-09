"""도킹 진행 상태 판단.

ROS와 시계에 의존하지 않는 순수 파이썬 모듈입니다. 경과 시간을 인자로
받으므로 "마커를 3초간 놓쳤을 때" 같은 시나리오를 실제로 기다리지 않고
테스트할 수 있습니다.

단계 전이:

    SEARCHING ─(마커 보임)─> APPROACHING ─(거리 도달)─> ALIGNING
                                                          │
                                       (각도까지 맞음) ───┘
                                                          ↓
                                                     CONFIRMING
                                              (스테이션 접점 또는 타임아웃)
                                                          ↓
                                                       DOCKED
"""

import math
from dataclasses import dataclass
from enum import Enum

from potner_docking.approach_controller import DockingGains, compute


class DockingPhase(Enum):
    SEARCHING = "SEARCHING"
    APPROACHING = "APPROACHING"
    ALIGNING = "ALIGNING"
    CONFIRMING = "CONFIRMING"
    TURNING = "TURNING"
    DOCKED = "DOCKED"
    FAILED = "FAILED"


@dataclass
class SessionLimits:
    marker_lost_timeout: float = 2.0  # 이 시간 넘게 마커를 못 보면 정지
    docking_timeout: float = 90.0  # 전체 제한. 무한 루프 방지
    confirm_timeout: float = 5.0  # 스테이션 접점을 기다리는 시간

    # 목표 마커를 한 번도 못 본 채 이 시간이 지나면 포기합니다. 전체
    # 제한만 두면 마커가 없는 자리에서 90초를 서 있게 됩니다.
    search_timeout: float = 15.0

    # 정렬 후 제자리 회전에 주는 시간. 이걸 넘기면 바퀴가 헛돌거나
    # 오도메트리가 죽은 것이라 접습니다. 180도를 0.5rad/s 로 돌면 약 6.3초.
    turn_timeout: float = 20.0

    # 스테이션 홀 센서(A3144) 신호를 성공 조건으로 요구할지. 스테이션이
    # 아직 없으므로 기본값은 False 입니다. 완성되면 True 로 바꿔서 물리적
    # 접점까지 확인하게 하세요.
    require_station_confirm: bool = False


@dataclass
class DockingStep:
    phase: DockingPhase
    linear: float
    angular: float
    reason: str
    # 이번 주기에 실제로 본 관측. 마커를 놓쳤으면 None 입니다. 액션 피드백이
    # 옛 값을 계속 보여주지 않게 하려고 들고 다닙니다.
    observation: tuple = None

    @property
    def finished(self) -> bool:
        return self.phase in (DockingPhase.DOCKED, DockingPhase.FAILED)

    @property
    def succeeded(self) -> bool:
        return self.phase is DockingPhase.DOCKED


class DockingSession:
    """한 번의 도킹 시도. 액션 목표 하나에 세션 하나가 대응합니다."""

    def __init__(self, gains: DockingGains = None, limits: SessionLimits = None):
        self.gains = gains or DockingGains()
        self.limits = limits or SessionLimits()
        self.phase = DockingPhase.SEARCHING
        self.last_observation = None
        self._aligned_since = None
        self._ever_seen = False
        self._turn_started_at = None

    def step(
        self,
        elapsed: float,
        marker_age: float,
        observation,
        station_confirmed: bool = False,
        turn_progress: float = 0.0,
    ) -> DockingStep:
        """다음 주행 명령과 단계를 계산합니다.

        Args:
            elapsed: 도킹 시작 후 경과 시간 (s)
            marker_age: 목표 마커를 마지막으로 본 뒤 경과 시간 (s).
                한 번도 못 봤으면 float('inf')
            observation: (거리 m, 좌우오차 px, 기울기 deg) 또는 None
            station_confirmed: 스테이션 홀 센서 접점 여부
            turn_progress: 회전 단계에 들어간 뒤 실제로 돈 각도 (rad, 크기).
                TURNING 이 아닐 때는 쓰이지 않습니다
        """
        if station_confirmed:
            return self._finish(DockingPhase.DOCKED, "스테이션 접점 확인")

        if elapsed > self.limits.docking_timeout:
            return self._finish(DockingPhase.FAILED, "전체 시간 초과")

        # ★ 회전 중에는 마커 유실 검사를 건너뜁니다. 등을 돌리는 동작이라
        #   마커가 시야에서 사라지는 것이 정상인데, 아래 유실 처리로 가면
        #   회전을 시작하자마자 SEARCHING 으로 떨어져 영영 못 돕니다.
        if self.phase is DockingPhase.TURNING:
            return self._turn_step(elapsed, turn_progress)

        # 마커를 놓쳤으면 멈춥니다. 안 보이는 채로 계속 전진하면 스테이션을
        # 들이받습니다.
        if observation is None or marker_age > self.limits.marker_lost_timeout:
            self.phase = DockingPhase.SEARCHING
            self._aligned_since = None

            # 한 번도 못 본 채 탐색 제한을 넘겼으면 접습니다. 엉뚱한 자리에
            # 도착했다는 뜻이라 더 기다려도 달라지지 않습니다.
            if not self._ever_seen and elapsed > self.limits.search_timeout:
                return self._finish(DockingPhase.FAILED, "마커를 찾지 못함")

            return DockingStep(self.phase, 0.0, 0.0, "마커 유실")

        self._ever_seen = True
        self.last_observation = observation
        distance, lateral, yaw = observation
        command = compute(distance, lateral, yaw, self.gains)

        if command.docked:
            if self._aligned_since is None:
                self._aligned_since = elapsed
            waited = elapsed - self._aligned_since

            if waited >= self.limits.confirm_timeout:
                if self.limits.require_station_confirm:
                    return self._finish(
                        DockingPhase.FAILED, "스테이션 접점 신호 없음", observation
                    )
                return self._begin_turn(elapsed, observation)

            self.phase = DockingPhase.CONFIRMING
            return DockingStep(
                self.phase, 0.0, 0.0, "스테이션 확인 대기", observation
            )

        # 정렬이 풀렸으면 대기 타이머를 초기화합니다. 마커가 흔들려 잠깐
        # 정렬됐다 풀린 것을 성공으로 치면 단자가 안 맞은 채로 끝납니다.
        self._aligned_since = None
        self.phase = (
            DockingPhase.ALIGNING if command.linear == 0.0 else DockingPhase.APPROACHING
        )
        return DockingStep(
            self.phase, command.linear, command.angular, command.reason, observation
        )

    def _begin_turn(self, elapsed, observation):
        """정렬이 끝났습니다. 화분을 스테이션 쪽으로 돌립니다.

        마커를 보고 붙은 자세 그대로면 화분이 반대편을 향하고 있어서
        급수·송풍·촬영 장치가 닿지 않습니다. 회전까지 끝나야 도킹이
        끝난 것이므로, 여기서 바로 DOCKED 를 내지 않습니다 — 액션이
        성공으로 끝나는 순간 mission_manager 가 서버에 OK 를 보내고
        서버는 곧바로 급수를 시킵니다.
        """
        if self.gains.turn_after_dock_deg <= 0.0:
            return self._finish(DockingPhase.DOCKED, "비전 정렬 완료", observation)

        self.phase = DockingPhase.TURNING
        self._turn_started_at = elapsed
        self.last_observation = observation
        return DockingStep(
            self.phase,
            0.0,
            self.gains.turn_speed,
            f"정렬 완료 — {self.gains.turn_after_dock_deg:.0f}도 회전 시작",
            observation,
        )

    def _turn_step(self, elapsed, turn_progress):
        target = math.radians(self.gains.turn_after_dock_deg)
        if turn_progress >= target:
            return self._finish(
                DockingPhase.DOCKED,
                f"정렬·회전 완료 ({math.degrees(turn_progress):.0f}도)",
                self.last_observation,
            )

        if elapsed - self._turn_started_at > self.limits.turn_timeout:
            # 바퀴가 헛돌거나 오도메트리가 안 오는 상황입니다. 덜 돈 채로
            # 성공을 내면 서버가 급수를 시켜 물이 엉뚱한 데로 갑니다.
            return self._finish(
                DockingPhase.FAILED,
                f"회전 시한 초과 ({math.degrees(turn_progress):.0f}도만 회전)",
                self.last_observation,
            )

        return DockingStep(
            self.phase,
            0.0,
            self.gains.turn_speed,
            f"회전 중 ({math.degrees(turn_progress):.0f}도)",
            self.last_observation,
        )

    def _finish(self, phase, reason, observation=None):
        self.phase = phase
        return DockingStep(phase, 0.0, 0.0, reason, observation)
