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
from typing import Optional

from potner_docking.approach_controller import DockingGains, compute
from potner_docking.marker_search import MarkerSearch, SearchConfig

# 회전 정체 수용 — 감속 바닥 속도가 정지마찰을 못 이기면 목표 몇 도 앞에서
# 영영 못 움직일 수 있습니다. TURNING 은 마커 유실 검사를 건너뛰어 출구가
# turn_timeout 뿐이라, 목표 코앞의 정체를 "다 돌아놓고 실패"로 만드는 대신
# 성공으로 받아들입니다. 목표에서 TURN_STALL_ACCEPT_DEG 안쪽 정체만 수용.
TURN_STALL_ACCEPT_DEG = 5.0
TURN_STALL_WINDOW = 2.0  # s. 이 시간 동안 안 움직이면 정체로 판정
TURN_STALL_EPSILON = 0.01  # rad (~0.6도). 이보다 늘어야 "움직였다"로 침


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
    # 마커를 놓친 뒤 이 시간까지는 **멈춰서** 재검출을 기다리고, 넘겨야
    # 탐색 회전을 시작합니다. 놓침의 대부분은 자기 움직임이 만든 번짐이라
    # 멈추면 몇 프레임 안에 다시 잡히는데, 곧바로 돌기 시작하면 방금까지
    # 잘 보이던 마커를 화면 밖으로 밀어내 버립니다 (실기에서 당했습니다).
    marker_lost_timeout: float = 2.0
    docking_timeout: float = 90.0  # 전체 제한. 무한 루프 방지
    confirm_timeout: float = 5.0  # 스테이션 접점을 기다리는 시간

    # 목표 마커를 한 번도 못 본 채 이 시간이 지나면 포기합니다.
    #
    # ★ 0 이하면 이 제한을 쓰지 않고 전체 제한(docking_timeout)까지 계속
    #   찾습니다. 예전에는 15초였는데, 탐색 회전이 한 바퀴 도는 데만
    #   약 16초라 한 바퀴도 못 돌고 포기했습니다.
    search_timeout: float = 0.0

    # 정렬 후 제자리 회전에 주는 시간. 이걸 넘기면 바퀴가 헛돌거나
    # 오도메트리가 죽은 것이라 접습니다. 끝 감속 포함 180도에 약 8~9초.
    turn_timeout: float = 20.0

    # 목표 거리에 도착한 뒤 이 시간까지 정렬이 안 끝나면 결단합니다.
    #
    # ★ 제자리 회전은 좌우 오차와 기울기를 강체로 함께 움직여서, 두 보정
    #   항이 상쇄되는 평형(angular≈0)에 갇히면 잔류가 영영 안 빠집니다 —
    #   후진이 없어 물러났다 다시 붙을 수도 없습니다. 그때 전체 제한
    #   90초를 다 태우는 대신: 잔류가 허용치의 2배 안이면 수용하고 회전
    #   으로 넘어가고, 그보다 크면 일찍 실패해 재시도 기회를 줍니다.
    #   0 이하면 이 장치를 끕니다.
    align_timeout: float = 15.0

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
    # True 면 호출하는 쪽이 회전·이동 누적기를 0 으로 되돌려야 합니다.
    # 탐색 단계가 바뀌는 순간이라 이전 단계의 누적이 섞이면 안 됩니다.
    restart_odometry: bool = False

    @property
    def finished(self) -> bool:
        return self.phase in (DockingPhase.DOCKED, DockingPhase.FAILED)

    @property
    def succeeded(self) -> bool:
        return self.phase is DockingPhase.DOCKED


class DockingSession:
    """한 번의 도킹 시도. 액션 목표 하나에 세션 하나가 대응합니다."""

    def __init__(
        self,
        gains: DockingGains = None,
        limits: SessionLimits = None,
        search: SearchConfig = None,
    ):
        self.gains = gains or DockingGains()
        self.limits = limits or SessionLimits()
        self.search = MarkerSearch(search)
        self._last_lateral = 0.0
        self.phase = DockingPhase.SEARCHING
        self.last_observation = None
        self._aligned_since = None
        self._ever_seen = False
        self._turn_started_at = None
        self._close_since = None
        self._turn_last_progress = 0.0
        self._turn_last_advance = 0.0

    def step(
        self,
        elapsed: float,
        marker_age: float,
        observation: Optional[tuple],
        station_confirmed: bool = False,
        turn_progress: float = 0.0,
        creep_progress: float = 0.0,
        front_range: float = math.inf,
    ) -> DockingStep:
        """다음 주행 명령과 단계를 계산합니다.

        Args:
            elapsed: 도킹 시작 후 경과 시간 (s)
            marker_age: 목표 마커를 마지막으로 본 뒤 경과 시간 (s).
                한 번도 못 봤으면 float('inf')
            observation: (거리 m, 좌우오차 px, 기울기 deg) 또는 None
            station_confirmed: 스테이션 홀 센서 접점 여부
            turn_progress: 지금 단계에 들어온 뒤 돌아간 각도 (rad, 크기)
            creep_progress: 지금 단계에 들어온 뒤 이동한 거리 (m, 크기)
            front_range: 정면에서 가장 가까운 장애물까지 거리 (m).
                탐색 중 전진해도 되는지 판단하는 데만 씁니다
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

        # 마커를 놓쳤습니다. 예전에는 여기서 0,0 을 내고 가만히 서 있었는데,
        # 장면이 안 바뀌니 영영 못 찾았습니다. 이제는 돌면서 찾습니다.
        if observation is None or marker_age > self.limits.marker_lost_timeout:
            entering = self.phase is not DockingPhase.SEARCHING
            self.phase = DockingPhase.SEARCHING
            self._aligned_since = None

            if entering:
                # 방금 놓쳤다면 마지막으로 본 쪽으로 돕니다. 반대로 돌면
                # 마커에서 더 멀어져 한 바퀴를 헛돕니다.
                #   lateral > 0 = 마커가 화면 오른쪽 -> 오른쪽으로 돌아야
                #   하고, 그건 angular 가 음수라는 뜻입니다.
                self.search.reset(-1.0 if self._last_lateral > 0.0 else 1.0)
                # 이전 단계에서 쌓인 회전·이동량이 섞이면 들어오자마자
                # "한 바퀴 다 돌았다" 로 오판합니다.
                turn_progress = 0.0
                creep_progress = 0.0

            # search_timeout 이 0 이하면 전체 제한까지 계속 찾습니다.
            if (
                self.limits.search_timeout > 0.0
                and not self._ever_seen
                and elapsed > self.limits.search_timeout
            ):
                return self._finish(DockingPhase.FAILED, "마커를 찾지 못함")

            # ★ 방금 전까지 보였다면 아직 돌지 않습니다. 카메라가 한 프레임
            #   놓치는 건 (회전 번짐 때문에) 흔한 일인데, 그때마다 즉시 탐색
            #   회전을 시작하면 그 회전이 마커를 화면 밖으로 밀어냅니다 —
            #   실기에서 "잘 찾다가도 놓치는" 원인이었습니다. 멈춰 있으면
            #   번짐이 사라져 보통 다음 몇 프레임 안에 다시 잡힙니다.
            #   restart_odometry 로 누적기를 계속 비워, 회전을 시작할 때
            #   대기 중 흘러든 각도가 한 바퀴 판정에 섞이지 않게 합니다.
            if marker_age <= self.limits.marker_lost_timeout:
                return DockingStep(
                    self.phase, 0.0, 0.0, "마커 재검출 대기", None,
                    restart_odometry=True,
                )

            found = self.search.step(
                turn_progress, creep_progress, front_range, elapsed
            )
            return DockingStep(
                self.phase,
                found.linear,
                found.angular,
                found.reason,
                None,
                restart_odometry=found.restart_odometry,
            )

        self._ever_seen = True
        self.last_observation = observation
        distance, lateral, yaw = observation
        self._last_lateral = lateral
        if distance > self.gains.target_distance:
            # ★ 목표 거리 밖이면 정렬 시한 시계를 되감습니다. 유실 후
            #   멀리서 다시 찾은 경우인데, 낡은 시계로 아래 잔류 수용이
            #   발동하면 스테이션에서 0.5m 떨어진 채 "성공"이 나가고
            #   서버가 그 자리에서 급수를 시작합니다.
            self._close_since = None
        elif self._close_since is None:
            self._close_since = elapsed
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

        # ★ 정렬 교착 탈출 — SessionLimits.align_timeout 의 주석 참고.
        #   제자리 회전은 두 보정 항이 상쇄되는 평형에 갇힐 수 있고, 그러면
        #   전체 제한 90초를 그 자리에서 다 태웁니다 (시뮬레이션에서 5도만
        #   비스듬히 출발해도 67~80% 가 이 경로로 실패했습니다).
        if (
            self.limits.align_timeout > 0.0
            and self._close_since is not None
            and elapsed - self._close_since > self.limits.align_timeout
        ):
            acceptable = (
                abs(lateral) <= 2.0 * self.gains.lateral_tolerance
                and abs(yaw) <= 2.0 * self.gains.yaw_tolerance
            )
            if acceptable:
                return self._begin_turn(
                    elapsed,
                    observation,
                    reason=(
                        f"정렬 시한 초과 — 잔류 수용 "
                        f"(좌우 {lateral:.0f}px, 기울기 {yaw:.0f}도)"
                    ),
                )
            return self._finish(
                DockingPhase.FAILED,
                f"정렬 시한 초과 (좌우 {lateral:.0f}px, 기울기 {yaw:.0f}도 잔류)",
                observation,
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

    def _begin_turn(self, elapsed, observation, reason=None):
        """정렬이 끝났습니다. 화분을 스테이션 쪽으로 돌립니다.

        마커를 보고 붙은 자세 그대로면 화분이 반대편을 향하고 있어서
        급수·송풍·촬영 장치가 닿지 않습니다. 회전까지 끝나야 도킹이
        끝난 것이므로, 여기서 바로 DOCKED 를 내지 않습니다 — 액션이
        성공으로 끝나는 순간 mission_manager 가 서버에 OK 를 보내고
        서버는 곧바로 급수를 시킵니다.
        """
        if self.gains.turn_after_dock_deg <= 0.0:
            return self._finish(
                DockingPhase.DOCKED, reason or "비전 정렬 완료", observation
            )

        self.phase = DockingPhase.TURNING
        self._turn_started_at = elapsed
        self._turn_last_progress = 0.0
        self._turn_last_advance = elapsed
        self.last_observation = observation
        return DockingStep(
            self.phase,
            0.0,
            self.gains.turn_speed,
            f"{reason or '정렬 완료'} — {self.gains.turn_after_dock_deg:.0f}도 회전 시작",
            observation,
        )

    def _turn_step(self, elapsed, turn_progress):
        target = math.radians(self.gains.turn_after_dock_deg)
        remaining = target - turn_progress

        # ★ 마진만큼 일찍 멈춥니다. 정지 명령이 물리 정지가 되기까지
        #   0.2~0.5초가 걸려 그동안은 그대로 지나갑니다 — 감속 없이
        #   0.5rad/s 로 문턱을 넘던 때는 이 지연이 +10도였습니다 (실측
        #   190도). 감속과 마진의 근거는 DockingGains.turn_slow_angle_deg
        #   주석에 있습니다.
        if remaining <= math.radians(self.gains.turn_stop_margin_deg):
            return self._finish(
                DockingPhase.DOCKED,
                f"정렬·회전 완료 ({math.degrees(turn_progress):.0f}도)",
                self.last_observation,
            )

        if turn_progress > self._turn_last_progress + TURN_STALL_EPSILON:
            self._turn_last_progress = turn_progress
            self._turn_last_advance = elapsed
        elif (
            elapsed - self._turn_last_advance > TURN_STALL_WINDOW
            and remaining <= math.radians(TURN_STALL_ACCEPT_DEG)
        ):
            # 감속 바닥이 정지마찰을 못 이겨 목표 코앞에서 멈춘 경우.
            # 여기서 계속 버티면 "178도 돌아놓고 시한 초과 실패"가 됩니다.
            return self._finish(
                DockingPhase.DOCKED,
                f"회전 정체 — 잔여 {math.degrees(remaining):.0f}도 수용",
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

        # 끝이 가까우면 남은 각도에 비례해 감속합니다. copysign 은 회전
        # 방향(turn_speed 부호)을 지키기 위한 것 — 크기만 max 로 바닥을
        # 깔면 음수 속도(우회전 설정)일 때 감속 구간에서 부호가 뒤집혀
        # 경계에서 영영 왔다갔다 합니다.
        speed = self.gains.turn_speed
        slow = math.radians(self.gains.turn_slow_angle_deg)
        if slow > 0.0 and remaining < slow:
            speed = math.copysign(
                max(
                    self.gains.turn_min_speed,
                    abs(self.gains.turn_speed) * remaining / slow,
                ),
                self.gains.turn_speed,
            )

        return DockingStep(
            self.phase,
            0.0,
            speed,
            f"회전 중 ({math.degrees(turn_progress):.0f}도)",
            self.last_observation,
        )

    def _finish(self, phase, reason, observation=None):
        self.phase = phase
        return DockingStep(phase, 0.0, 0.0, reason, observation)
