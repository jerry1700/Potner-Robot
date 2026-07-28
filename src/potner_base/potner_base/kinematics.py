"""차동 구동(Differential Drive) 기구학 계산.

ROS에 의존하지 않는 순수 파이썬 모듈입니다. 젯슨 없이 노트북에서도 돌고,
CI에서 단위 테스트로 검증됩니다. rclpy를 import 하지 마세요.

하드웨어 기준값 (DFRobot FIT0403):
    - 감속비 90:1, 무부하 122 RPM
    - 엔코더 출력축 기준 1440 CPR
"""

import math
from dataclasses import dataclass

# 32비트 정수 카운터 래핑 처리용 상수
_INT32_SPAN = 1 << 32
_INT32_HALF = 1 << 31


@dataclass
class DriveConfig:
    """로봇 실측값. potner_bringup/config/potner_params.yaml 에서 주입됩니다.

    TODO: 조립 후 실측값으로 교체할 것. 아래는 임시 기본값입니다.
    """

    wheel_diameter: float = 0.065  # 바퀴 지름 (m) — 실측 필요
    wheel_separation: float = 0.200  # 좌우 바퀴 중심 간 거리 (m) — 실측 필요
    counts_per_rev: int = 1440  # FIT0403 출력축 CPR (확정값)
    max_wheel_speed: float = 0.25  # 바퀴 선속도 상한 (m/s) — 안전 제한

    @property
    def wheel_circumference(self) -> float:
        return math.pi * self.wheel_diameter

    @property
    def meters_per_count(self) -> float:
        return self.wheel_circumference / self.counts_per_rev


def twist_to_wheel_speeds(linear, angular, cfg):
    """cmd_vel(선속도·각속도) → 좌/우 바퀴 선속도(m/s).

    상한을 넘으면 좌우 비율을 유지한 채 같이 줄입니다. 한쪽만 자르면
    로봇이 의도하지 않은 방향으로 휘어집니다.
    """
    half_track = cfg.wheel_separation / 2.0
    left = linear - angular * half_track
    right = linear + angular * half_track

    peak = max(abs(left), abs(right))
    if peak > cfg.max_wheel_speed:
        scale = cfg.max_wheel_speed / peak
        left *= scale
        right *= scale

    return left, right


def wheel_speeds_to_twist(left, right, cfg):
    """좌/우 바퀴 선속도 → 선속도·각속도. 주로 검증용 역변환입니다."""
    linear = (left + right) / 2.0
    angular = (right - left) / cfg.wheel_separation
    return linear, angular


def tick_delta(previous, current):
    """32비트 카운터의 래핑을 고려한 증분 계산.

    ESP32는 누적 카운트를 int32로 보냅니다. 오버플로 지점에서 단순 뺄셈을
    하면 40억 가까운 값이 튀어나와 오도메트리가 순간이동합니다.
    """
    delta = (current - previous) % _INT32_SPAN
    if delta >= _INT32_HALF:
        delta -= _INT32_SPAN
    return delta


class OdometryIntegrator:
    """엔코더 카운트를 누적해 로봇의 위치(x, y, theta)를 추정합니다.

    Nav2와 AMCL이 이 값을 기반으로 동작하므로, 여기가 틀리면 자율주행
    전체가 무너집니다. 바퀴 지름과 축간거리 실측이 중요한 이유입니다.
    """

    def __init__(self, cfg):
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

    def update(self, left_ticks, right_ticks, dt):
        """누적 엔코더 카운트와 경과 시간(s)으로 자세를 갱신합니다."""
        if dt <= 0.0:
            return self.pose

        # 첫 샘플은 기준점만 잡고 이동량을 계산하지 않습니다.
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

        # 중점 적분: 구간 중앙의 방향을 써야 곡선 주행 오차가 줄어듭니다.
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


def _normalize_angle(angle):
    """각도를 -pi ~ +pi 범위로 정규화합니다."""
    return math.atan2(math.sin(angle), math.cos(angle))
