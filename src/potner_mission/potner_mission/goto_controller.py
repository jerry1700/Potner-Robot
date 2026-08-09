"""두 좌표 사이를 직접 주행하는 제어 (Nav2 없이).

ROS 에 의존하지 않는 순수 파이썬 모듈입니다. 현재 자세와 목표 자세를
넣으면 다음 주행 명령이 나옵니다.

★ 왜 Nav2 를 안 쓰는가 — 시연 환경이 좁아서입니다. Nav2 는 지도와
  코스트맵으로 **장애물을 피해 돌아가는** 것이 값어치인데, 가벽으로
  만든 좁은 공간에서는 기본 여유(55cm)만으로도 갈 수 있는 칸이 남지
  않습니다. 게다가 지도·AMCL 초기 위치까지 얹히면 시연 중에 손댈 것이
  너무 많아집니다.

  이 제어기는 그 대신 **오도메트리만 보고 목표로 직진**합니다. 장애물을
  피하지는 못하고 safety_node 가 세울 뿐입니다. 대신 정밀도가 필요한
  구간(스테이션 앞)은 어차피 도킹 제어기가 맡으므로, 여기서는 마커가
  카메라에 들어올 만큼만 데려다주면 됩니다.

★ 좌표계는 오도메트리 원점 기준입니다. 로봇을 재시작하면 원점이 그
  자리로 새로 잡혀 **등록해 둔 좌표가 전부 무의미해집니다.** 바닥에
  원점을 표시해 두고 늘 같은 자리·같은 방향에서 띄우세요.
"""

import math
from dataclasses import dataclass
from enum import Enum


class GotoPhase(Enum):
    DRIVING = "DRIVING"  # 목표 지점으로 이동 중
    FINAL_TURN = "FINAL_TURN"  # 지점에 도착, 목표 방향으로 회전 중
    ARRIVED = "ARRIVED"


@dataclass(frozen=True)
class Pose:
    """평면 자세. x, y 는 m, yaw 는 rad (좌회전이 +)."""

    x: float
    y: float
    yaw: float


@dataclass(frozen=True)
class GotoConfig:
    """주행 설정.

    속도 기본값은 base_driver 의 max_wheel_speed(0.25 m/s)보다 넉넉히
    낮게 잡았습니다. 상한에 붙여 두면 회전 성분이 얹힐 때 한쪽 바퀴가
    포화되어, 명령한 곡률과 실제 곡률이 달라집니다.
    """

    cruise_speed: float = 0.18  # m/s
    # 목표에 이만큼 남으면 감속을 시작합니다. 정지 명령이 물리 정지가
    # 되기까지 0.2~0.5초가 걸려서(20Hz 주기 + 시리얼 + PID + 관성),
    # 전속으로 목표를 지나면 그 지연만큼 지나칩니다 — 0.18m/s 면 5~9cm.
    slow_distance: float = 0.35  # m
    min_speed: float = 0.05  # m/s. 감속 바닥 — 정지마찰에 걸리지 않을 만큼

    # 진행 방향 보정. 목표 방위와 현재 헤딩의 차에 이 이득을 곱합니다.
    kp_heading: float = 1.2  # rad/s per rad
    max_angular: float = 0.8  # rad/s

    # 제자리 회전(마지막 방향 맞추기) 설정. 도킹 후 회전과 같은 이유로
    # 끝에서 감속합니다 — 전속으로 문턱을 지나면 정지 지연이 그대로
    # 초과 회전이 됩니다.
    turn_speed: float = 0.5  # rad/s
    turn_slow_angle_deg: float = 45.0
    turn_min_speed: float = 0.15  # rad/s

    # 도착 판정.
    #
    # ★ 이 여유가 곧 도킹의 출발 자세가 됩니다. 스테이션 50cm 앞을
    #   목표로 등록해 두면, 여기서 8cm 어긋난 채 서더라도 마커 방위는
    #   atan(0.08/0.5) = 9도라 카메라 시야(반각 18도) 안에 들어옵니다.
    #   15cm 로 두면 17도가 되어 시야 경계에 걸립니다 — 도킹이 마커를
    #   못 보고 탐색 회전부터 시작하게 됩니다.
    #
    #   더 조여도 시뮬레이션에서는 떨림 없이 수렴하지만, 오도메트리
    #   잡음을 쫓아다니게 되므로 이 정도가 균형점입니다.
    position_tolerance: float = 0.08  # m
    # ★ 방향 여유는 그대로 도킹의 마커 방위 오차가 됩니다. 여기서 멈춘
    #   자세로 스테이션을 보게 되므로, 10도로 두면 위치 오차 8cm(9도)와
    #   합쳐 19도가 되어 카메라 시야(반각 18도) 밖으로 나갑니다 — 도킹이
    #   마커를 못 보고 탐색 회전부터 시작합니다. 5도면 합쳐서 14도라
    #   여유가 남습니다. 감속 바닥에서의 정지 지연이 2.6도라 그보다
    #   넉넉합니다.
    yaw_tolerance_deg: float = 5.0

    # 주행 제한. 넘으면 실패로 접습니다. 바퀴가 헛돌거나 오도메트리가
    # 죽었는데 계속 명령을 내면 로봇이 벽을 밀고 있게 됩니다.
    timeout: float = 60.0  # s


@dataclass(frozen=True)
class GotoStep:
    linear: float
    angular: float
    phase: GotoPhase
    reason: str
    distance: float  # 목표 지점까지 남은 거리 (m)
    yaw_error: float  # 목표 방향까지 남은 각도 (rad, 부호 있음)

    @property
    def arrived(self) -> bool:
        return self.phase is GotoPhase.ARRIVED


def shortest_angle_diff(target: float, source: float) -> float:
    """``source`` 에서 ``target`` 까지의 최단 각도 차 (rad, -pi~pi).

    potner_docking.turn_tracker 에도 같은 식이 있습니다. 패키지 경계를
    넘는 의존을 만들지 않으려고 한 줄을 각자 둡니다.
    """
    return math.atan2(math.sin(target - source), math.cos(target - source))


class GotoNavigator:
    """목표 하나를 향한 주행. 목표마다 새로 만들거나 reset() 하세요."""

    def __init__(self, config: GotoConfig = None):
        self.config = config or GotoConfig()
        self._reached_position = False

    @property
    def reached_position(self) -> bool:
        return self._reached_position

    def reset(self) -> None:
        self._reached_position = False

    def step(self, current: Pose, goal: Pose) -> GotoStep:
        """다음 주행 명령을 계산합니다.

        Args:
            current: 오도메트리가 알려주는 지금 자세
            goal: 서버가 준 목표 자세
        """
        cfg = self.config
        dx = goal.x - current.x
        dy = goal.y - current.y
        distance = math.hypot(dx, dy)
        yaw_error = shortest_angle_diff(goal.yaw, current.yaw)

        # ★ 한 번 도착하면 다시 주행으로 돌아가지 않습니다. 목표 코앞에서
        #   위치 오차가 여유를 들락날락하면 "가다 서다"를 반복하는데,
        #   그 사이 방향이 계속 바뀌어 영영 안 끝납니다.
        if distance <= cfg.position_tolerance:
            self._reached_position = True

        if self._reached_position:
            return self._final_turn(distance, yaw_error)

        return self._drive(dx, dy, distance, current.yaw, yaw_error)

    def _drive(self, dx, dy, distance, current_yaw, yaw_error):
        cfg = self.config
        bearing = math.atan2(dy, dx)
        heading_error = shortest_angle_diff(bearing, current_yaw)

        angular = _clamp(cfg.kp_heading * heading_error, cfg.max_angular)

        # ★ 방향이 틀어진 만큼 전진을 줄입니다 (cos). 90도 이상 틀어져
        #   있으면 0 이 되어 제자리 회전이 되고, 정면을 보면 전속이
        #   됩니다. 문턱으로 "회전 먼저 / 그다음 주행"을 나누면 그 문턱
        #   에서 두 모드를 오가며 떠는데, 이렇게 하면 경계가 없습니다.
        cruise = cfg.cruise_speed * max(0.0, math.cos(heading_error))

        linear = cruise
        if distance < cfg.slow_distance:
            linear = cruise * (distance / cfg.slow_distance)

        # 감속 바닥. 단 cruise 자체가 낮으면(옆이나 뒤를 보고 있으면)
        # 그보다 높이지 않습니다 — 그랬다간 엉뚱한 방향으로 밀고 갑니다.
        if cruise > 0.0:
            linear = max(min(cfg.min_speed, cruise), linear)

        if cruise <= 0.0:
            reason = "목표 방향으로 회전 중"
        elif distance < cfg.slow_distance:
            reason = f"목표 접근 — 감속 ({distance:.2f}m)"
        else:
            reason = f"주행 중 ({distance:.2f}m)"

        return GotoStep(
            linear, angular, GotoPhase.DRIVING, reason, distance, yaw_error
        )

    def _final_turn(self, distance, yaw_error):
        cfg = self.config
        remaining = abs(yaw_error)

        if remaining <= math.radians(cfg.yaw_tolerance_deg):
            return GotoStep(
                0.0, 0.0, GotoPhase.ARRIVED, "도착", distance, yaw_error
            )

        speed = cfg.turn_speed
        slow = math.radians(cfg.turn_slow_angle_deg)
        if slow > 0.0 and remaining < slow:
            speed = max(cfg.turn_min_speed, cfg.turn_speed * remaining / slow)

        return GotoStep(
            0.0,
            math.copysign(speed, yaw_error),
            GotoPhase.FINAL_TURN,
            f"방향 맞추는 중 ({math.degrees(remaining):.0f}도)",
            distance,
            yaw_error,
        )


def _clamp(value: float, limit: float) -> float:
    return math.copysign(min(abs(value), limit), value)
