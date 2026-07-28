"""도킹 진행 상태 판단.

ROS와 시계에 의존하지 않는 순수 로직입니다. 경과 시간을 인자로 받기
때문에 테스트에서 임의의 시나리오를 즉시 재현할 수 있습니다. "마커를
3초간 놓쳤을 때 멈추는가" 같은 검증을 실제로 3초 기다리지 않고 합니다.

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

from dataclasses import dataclass
from enum import Enum

from potner_docking.approach_controller import DockingGains, compute


class DockingPhase(Enum):
    SEARCHING = "SEARCHING"
    APPROACHING = "APPROACHING"
    ALIGNING = "ALIGNING"
    CONFIRMING = "CONFIRMING"
    DOCKED = "DOCKED"
    FAILED = "FAILED"


@dataclass
class SessionLimits:
    marker_lost_timeout: float = 2.0  # 이 시간 넘게 마커를 못 보면 정지
    docking_timeout: float = 90.0  # 전체 제한. 무한 루프 방지
    confirm_timeout: float = 5.0  # 스테이션 접점을 기다리는 시간

    # 스테이션 홀 센서(A3144) 신호를 도킹 성공 조건으로 요구할지.
    # 스테이션이 아직 없으므로 기본값은 False 입니다. 스테이션이
    # 완성되면 True 로 바꿔 물리적 접점을 확인하게 하세요.
    require_station_confirm: bool = False


@dataclass
class DockingStep:
    phase: DockingPhase
    linear: float
    angular: float
    reason: str

    @property
    def finished(self):
        return self.phase in (DockingPhase.DOCKED, DockingPhase.FAILED)

    @property
    def succeeded(self):
        return self.phase is DockingPhase.DOCKED


class DockingSession:
    """한 번의 도킹 시도. 액션 목표 하나에 세션 하나가 대응합니다."""

    def __init__(self, gains=None, limits=None):
        self.gains = gains or DockingGains()
        self.limits = limits or SessionLimits()
        self.phase = DockingPhase.SEARCHING
        self.last_observation = None
        self._aligned_since = None

    def step(self, elapsed, marker_age, observation, station_confirmed=False):
        """다음 주행 명령과 단계를 계산합니다.

        Args:
            elapsed: 도킹 시작 후 경과 시간 (s)
            marker_age: 목표 마커를 마지막으로 본 뒤 경과 시간 (s).
                        한 번도 못 봤으면 float('inf')
            observation: (거리 m, 좌우오차 px, 기울기 deg) 또는 None
            station_confirmed: 스테이션 홀 센서 접점 여부
        """
        if station_confirmed:
            return self._finish(DockingPhase.DOCKED, "스테이션 접점 확인")

        if elapsed > self.limits.docking_timeout:
            return self._finish(DockingPhase.FAILED, "전체 시간 초과")

        # 마커를 놓쳤으면 멈춥니다. 안 보이는 채로 계속 전진하면
        # 스테이션을 들이받습니다.
        if observation is None or marker_age > self.limits.marker_lost_timeout:
            self.phase = DockingPhase.SEARCHING
            self._aligned_since = None
            return DockingStep(self.phase, 0.0, 0.0, "마커 유실")

        self.last_observation = observation
        distance, lateral, yaw = observation
        command = compute(distance, lateral, yaw, self.gains)

        if command.docked:
            # 비전 기준으로는 정렬이 끝났습니다. 스테이션 접점을 기다립니다.
            if self._aligned_since is None:
                self._aligned_since = elapsed
            waited = elapsed - self._aligned_since

            if waited >= self.limits.confirm_timeout:
                if self.limits.require_station_confirm:
                    return self._finish(
                        DockingPhase.FAILED, "스테이션 접점 신호 없음"
                    )
                return self._finish(DockingPhase.DOCKED, "비전 정렬 완료")

            self.phase = DockingPhase.CONFIRMING
            return DockingStep(self.phase, 0.0, 0.0, "스테이션 확인 대기")

        # 정렬이 풀렸으면 대기 타이머를 초기화합니다.
        self._aligned_since = None
        self.phase = (
            DockingPhase.ALIGNING if command.linear == 0.0 else DockingPhase.APPROACHING
        )
        return DockingStep(self.phase, command.linear, command.angular, command.reason)

    def _finish(self, phase, reason):
        self.phase = phase
        return DockingStep(phase, 0.0, 0.0, reason)
