"""차동 구동(Differential Drive) 기구학과 오도메트리 적분.

ROS에 의존하지 않는 순수 파이썬 모듈입니다. 젯슨 없이 노트북에서도 돌고
CI에서 단위 테스트로 검증됩니다. rclpy를 import 하지 마세요.
"""

import math
from dataclasses import dataclass

_INT32_SPAN = 1 << 32
_INT32_HALF = 1 << 31


@dataclass
class DriveConfig:
    """로봇 실측값. potner_bringup/config/potner_params.yaml 에서 주입됩니다."""

    wheel_diameter: float = 0.060  # 확정 — 60mm 구동 바퀴
    # 실측 보정값. 자로 잰 바퀴 중심 거리는 0.230m 이지만,
    # 제자리 회전에서 타이어가 옆으로 비벼져 **회전에 실제로 작용하는
    # 축간거리는 그보다 짧습니다.** 2026-08-10 에 3바퀴 회전으로 실측해
    # 이 값을 얻었습니다 (tools/calibrate_turn.py).
    #
    # ★ 0.230 으로 되돌리지 마세요. 자로 잰 값과 다른 것이 정상입니다.
    #   0.230 이었을 때 오도메트리가 회전을 약 5% 적게 세서, 180도 회전
    #   명령에 몸이 190도를 돌고 좌표 주행도 엉뚱한 데로 갔습니다.
    #   URDF(potner.urdf.xacro)는 바퀴가 실제로 붙은 자리라 0.230 그대로 둡니다.
    #
    # ★ 바퀴나 바닥재를 바꾸면 다시 재세요:
    #     python3 tools/calibrate_turn.py
    wheel_separation: float = 0.2179
    # 사양서의 "1440 CPR" 은 채널당 사이클이고, 쿼드러처 4배수를 곱해야
    # 실제 카운트가 됩니다. 손으로 한 바퀴 돌려 5,941카운트로 실측 확인.
    counts_per_rev: int = 5760  # 실측 — 1440 CPR x 4 (쿼드러처)
    # 안전 제한 (m/s). 무부하 최고속도는 122RPM x 지름 60mm = 약 0.383
    max_wheel_speed: float = 0.25

    @property
    def wheel_circumference(self) -> float:
        return math.pi * self.wheel_diameter

    @property
    def meters_per_count(self) -> float:
        return self.wheel_circumference / self.counts_per_rev


def twist_to_wheel_speeds(linear: float, angular: float, cfg: DriveConfig):
    """cmd_vel 을 좌우 바퀴 선속도로 변환합니다.

    Args:
        linear: 선속도 (m/s)
        angular: 각속도 (rad/s)

    Returns:
        (좌 m/s, 우 m/s)
    """
    half_track = cfg.wheel_separation / 2.0
    left = linear - angular * half_track
    right = linear + angular * half_track

    # 상한을 넘으면 좌우 비율을 유지한 채 같이 줄입니다. 한쪽만 자르면
    # 로봇이 의도하지 않은 방향으로 휩니다.
    peak = max(abs(left), abs(right))
    if peak > cfg.max_wheel_speed:
        scale = cfg.max_wheel_speed / peak
        left *= scale
        right *= scale

    return left, right


def wheel_speeds_to_twist(left: float, right: float, cfg: DriveConfig):
    """좌우 바퀴 선속도를 cmd_vel 로 역변환합니다. 주로 검증용입니다."""
    linear = (left + right) / 2.0
    angular = (right - left) / cfg.wheel_separation
    return linear, angular


def tick_delta(previous: int, current: int) -> int:
    """32비트 카운터의 래핑을 고려한 증분.

    ESP32는 누적 카운트를 int32로 보냅니다. 오버플로 지점에서 단순 뺄셈을
    하면 40억 가까운 값이 튀어나와 오도메트리가 순간이동합니다.
    """
    delta = (current - previous) % _INT32_SPAN
    if delta >= _INT32_HALF:
        delta -= _INT32_SPAN
    return delta


class OdometryIntegrator:
    """엔코더 카운트를 누적해 로봇의 위치(x, y, theta)를 추정합니다.

    Nav2와 AMCL이 이 값을 기반으로 동작하므로, 여기가 틀리면 자율주행이
    통째로 무너집니다. 바퀴 지름과 축간거리 실측이 중요한 이유입니다.
    """

    def __init__(self, cfg: DriveConfig):
        self.cfg = cfg
        self.x = 0.0
        self.y = 0.0
        self.theta = 0.0
        self.linear_velocity = 0.0
        self.angular_velocity = 0.0
        self._prev_left = None
        self._prev_right = None

    def reset(self):
        self.__init__(self.cfg)

    def update(self, left_ticks: int, right_ticks: int, dt: float):
        """누적 엔코더 카운트와 경과 시간(s)으로 자세를 갱신합니다."""
        if dt <= 0.0:
            return self.pose

        # 첫 샘플은 기준점만 잡습니다. 전원을 켰을 때 카운터가 0이 아니어도
        # 순간이동하지 않아야 합니다.
        if self._prev_left is None:
            self._prev_left = left_ticks
            self._prev_right = right_ticks
            return self.pose

        d_left = tick_delta(self._prev_left, left_ticks) * self.cfg.meters_per_count
        d_right = tick_delta(self._prev_right, right_ticks) * self.cfg.meters_per_count
        self._prev_left = left_ticks
        self._prev_right = right_ticks

        d_center = (d_left + d_right) / 2.0
        d_theta = (d_right - d_left) / self.cfg.wheel_separation

        # 중점 적분. 구간 중앙의 방향을 써야 곡선 주행 오차가 줄어듭니다.
        mid_theta = self.theta + d_theta / 2.0
        self.x += d_center * math.cos(mid_theta)
        self.y += d_center * math.sin(mid_theta)
        self.theta = _normalize_angle(self.theta + d_theta)

        self.linear_velocity = d_center / dt
        self.angular_velocity = d_theta / dt
        return self.pose

    @property
    def pose(self):
        return self.x, self.y, self.theta


def _normalize_angle(angle: float) -> float:
    return math.atan2(math.sin(angle), math.cos(angle))
