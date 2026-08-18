"""제자리 회전량을 오도메트리로 누적하는 순수 로직.

ROS 에 의존하지 않으므로 CI 에서 검증됩니다.

★ 왜 최단각 차를 **누적**하는가 — 시작 yaw 와 현재 yaw 를 한 번에 빼면
  180도를 넘는 회전을 잴 수 없습니다. 최단각은 -pi~pi 로 접히므로 181도는
  -179도로 나오고, 정확히 180도는 부호조차 정해지지 않습니다. 도킹 후
  180도 회전이 바로 그 경계라서, 한 번 빼기로는 원리적으로 못 잽니다.

  대신 **연속한 두 표본 사이의 최단각 차**를 더해 나갑니다. 오도메트리가
  30Hz 로 오고 회전이 0.5 rad/s 면 표본 사이가 약 1도라, 접힐 일이
  없습니다. 이렇게 하면 180도든 360도든 잴 수 있습니다.

★ 이 값은 **오도메트리 기준**입니다. wheel_separation 설정이 실제와 다르면
  오도메트리와 실제 몸이 함께 어긋나는데, 명령을 바퀴 속도로 바꿀 때와
  엔코더에서 각도를 되계산할 때 같은 값을 쓰기 때문에 **오차가 상쇄되어
  오도메트리는 늘 명령한 만큼 돌았다고 보고합니다.** 즉 이 누적기로는
  그 오차를 잡을 수 없고, wheel_separation 을 실측해야만 실제 각도가
  맞습니다.
"""

import math


def yaw_from_quaternion(x: float, y: float, z: float, w: float) -> float:
    """쿼터니언에서 yaw(rad)를 뽑습니다."""
    return math.atan2(2.0 * (w * z + x * y), 1.0 - 2.0 * (y * y + z * z))


def shortest_angle_diff(target: float, source: float) -> float:
    """``source`` 에서 ``target`` 까지의 최단 각도 차 (rad, -pi~pi)."""
    return math.atan2(math.sin(target - source), math.cos(target - source))


class TurnAccumulator:
    """연속한 yaw 표본의 차를 더해 총 회전량을 셉니다."""

    def __init__(self):
        self._last = None
        self._total = 0.0

    @property
    def total(self) -> float:
        """부호 있는 누적 회전량 (rad). 좌회전이 +."""
        return self._total

    @property
    def turned(self) -> float:
        """돌아간 양의 크기 (rad). 목표와 비교할 때 씁니다."""
        return abs(self._total)

    @property
    def started(self) -> bool:
        return self._last is not None

    def reset(self) -> None:
        """회전을 새로 시작합니다. 다음 표본이 기준점이 됩니다."""
        self._last = None
        self._total = 0.0

    def update(self, yaw: float) -> float:
        """yaw 표본 하나를 반영하고 누적 회전량의 크기를 돌려줍니다.

        첫 표본은 기준점만 잡고 0 을 돌려줍니다.
        """
        if self._last is None:
            self._last = yaw
            return 0.0

        self._total += shortest_angle_diff(yaw, self._last)
        self._last = yaw
        return self.turned
