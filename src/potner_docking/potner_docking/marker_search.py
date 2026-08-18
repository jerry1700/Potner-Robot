"""마커를 못 찾았을 때 어떻게 움직여 찾을지 정하는 순수 로직.

ROS 에 의존하지 않으므로 CI 에서 검증됩니다.

예전에는 SEARCHING 이 ``0, 0`` 만 발행했습니다. 로봇이 가만히 서 있으니
장면이 바뀌지 않고, 그래서 한 번 놓치면 **영영 못 찾았습니다.** 실기에서
접근 도중 마커가 화면 밖으로 밀려나면 그대로 90초를 서 있다가 실패로
끝났습니다.

    한 바퀴 회전 ──(못 찾음)──> 조금 전진 ──> 다시 한 바퀴 ──> ...
         └──(찾음)──> APPROACHING

★ 전진 전에 **앞이 트였는지 반드시 확인**합니다. 이유가 있습니다.

  safety_node 는 라이다에 뭔가 가까이 잡히면 twist_mux 우선순위 255 로
  0 을 계속 쏩니다. 도킹은 150 이라 그 순간 **회전조차 못 하게 됩니다.**
  게다가 도킹 제어기에는 후진이 없어서 스스로 빠져나올 수도 없습니다.
  즉 한 번 붙으면 전체 제한시간까지 갇힙니다. 그래서 safety 가 걸리는
  거리까지 가기 전에 이쪽에서 먼저 멈춰야 합니다.

  범퍼 ToF 는 펌웨어가 최대값으로 고정해 두어 동작하지 않습니다. 바닥
  52cm 의 라이다가 유일한 눈이라, 그보다 낮은 물체는 이 검사로도 못
  막습니다 — 낮은 장애물이 있는 곳에서는 전진 탐색을 쓰지 마세요.
"""

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class SearchConfig:
    """탐색 동작 설정."""

    turn_speed: float = 0.4  # rad/s
    sweep_angle_deg: float = 360.0
    # ★ 스텝 회전 — 이만큼 돌고 **멈춰서** 카메라에 찾을 시간을 줍니다.
    #   연속으로 돌면 번짐 때문에 마커 위를 지나가도 검출이 안 됩니다
    #   (실기: 몇 바퀴를 돌아도 못 찾음). 스텝 각도는 시야(실효 36도)보다
    #   작아야 연속 멈춤끼리 겹쳐서 빈틈이 안 생깁니다. 정지 지연으로
    #   스텝마다 ~7도를 더 지나가므로 실제 간격은 설정값 + 7도입니다 —
    #   20도로 두면 실효 27도 대 시야 36도라 9도가 겹칩니다.
    step_angle_deg: float = 20.0
    pause_time: float = 1.0  # s. 멈춰서 보는 시간 (카메라 ~28fps 면 충분)
    # 한 바퀴 돌고도 못 찾았을 때 옮겨갈 거리.
    creep_speed: float = 0.06  # m/s
    creep_distance: float = 0.15  # m
    # 정면이 이보다 가까우면 전진하지 않습니다. safety 정지선(라이다
    # 기준 0.18m)보다 넉넉히 앞에서 멈춰야 갇히지 않습니다.
    front_clear_m: float = 0.50


@dataclass(frozen=True)
class SearchStep:
    linear: float
    angular: float
    reason: str
    # True 면 호출하는 쪽이 회전·전진 누적기를 0 으로 되돌려야 합니다.
    # 단계가 바뀌는 순간이라 이전 단계의 누적이 섞이면 안 됩니다.
    restart_odometry: bool = False


class MarkerSearch:
    """조금 돌고 멈춰 보기를 반복하다, 한 바퀴를 다 돌면 조금 이동하는 탐색."""

    ROTATING = "rotating"
    PAUSED = "paused"
    CREEPING = "creeping"

    def __init__(self, config: SearchConfig = None):
        self.config = config or SearchConfig()
        # ★ 돌기 전에 **먼저 봅니다.** 예전에는 회전부터 시작해서, 마커가
        #   이미 앞에 있어도 25도를 돌려 시야 밖으로 밀어냈고 그러면 한
        #   바퀴를 통째로 더 돌아야 했습니다. 회전 중에는 번짐 때문에
        #   검출이 안 되니, 놓친 자리에서 한 번 서서 확인하는 것이 항상
        #   먼저입니다 — 비용은 1초뿐입니다.
        self._mode = self.PAUSED
        self._direction = 1.0
        self._swept = 0.0  # 이번 한 바퀴에서 지금까지 돈 각도 (rad)
        self._paused_at = None

    @property
    def mode(self) -> str:
        return self._mode

    def reset(self, direction: float = 1.0) -> None:
        """탐색을 처음부터 시작합니다.

        Args:
            direction: 회전 방향. +1 이면 좌회전(CCW), -1 이면 우회전.
                접근 중에 놓친 경우라면 **마지막으로 본 쪽**을 주세요.
                반대로 돌면 마커에서 더 멀어져 한 바퀴를 헛돕니다.
        """
        self._mode = self.PAUSED  # 돌기 전에 먼저 본다 — __init__ 주석 참고
        self._direction = 1.0 if direction >= 0 else -1.0
        self._swept = 0.0
        self._paused_at = None

    def step(
        self,
        turned_rad: float,
        crept_m: float,
        front_range_m: float,
        elapsed: float = 0.0,
    ) -> SearchStep:
        """다음 탐색 동작을 정합니다.

        Args:
            turned_rad: 이 단계에 들어온 뒤 돌아간 각도 (rad, 크기)
            crept_m: 이 단계에 들어온 뒤 이동한 거리 (m, 크기)
            front_range_m: 정면에서 가장 가까운 장애물까지 거리 (m).
                모르면 ``math.inf`` 를 주세요 — 그러면 전진합니다
            elapsed: 도킹 시작 후 경과 시간 (s). 멈춰 보는 시간을 재는 데만
                씁니다
        """
        turn = self._direction * self.config.turn_speed
        blocked = front_range_m < self.config.front_clear_m

        # ★ 멈춰서 보는 중. 도는 동안에는 번짐 때문에 마커 위를 지나가도
        #   검출이 안 됩니다 — 멈춰야 선명한 프레임이 나옵니다.
        if self._mode == self.PAUSED:
            if self._paused_at is None:
                self._paused_at = elapsed
            if elapsed - self._paused_at < self.config.pause_time:
                return SearchStep(0.0, 0.0, "멈춰서 마커 확인 중")
            self._mode = self.ROTATING
            return SearchStep(0.0, turn, "다음 구간으로 회전", restart_odometry=True)

        if self._mode == self.CREEPING:
            if crept_m < self.config.creep_distance and not blocked:
                return SearchStep(self.config.creep_speed, 0.0, "탐색 전진 중")
            self._mode = self.ROTATING
            self._swept = 0.0
            reason = "앞이 막혀 회전으로 전환" if blocked else "전진 끝 — 다시 회전"
            return SearchStep(0.0, turn, reason, restart_odometry=True)

        # ROTATING — 한 스텝만 돌고 멈춰서 봅니다.
        step_rad = math.radians(self.config.step_angle_deg)
        if step_rad > 0.0 and turned_rad >= step_rad:
            self._swept += turned_rad
            if self._swept < math.radians(self.config.sweep_angle_deg):
                self._mode = self.PAUSED
                self._paused_at = elapsed
                return SearchStep(
                    0.0, 0.0,
                    f"멈춰서 마커 확인 ({math.degrees(self._swept):.0f}도 지점)",
                    restart_odometry=True,
                )
        elif turned_rad + self._swept < math.radians(self.config.sweep_angle_deg):
            return SearchStep(0.0, turn, "마커 탐색 회전")

        # 한 바퀴 다 돌았는데 못 찾았습니다.
        self._swept = 0.0
        if blocked:
            # 전진하면 갇히므로 그 자리에서 한 바퀴 더 돕니다. 사람이
            # 마커를 들고 다가오는 시연에서는 이것만으로도 충분합니다.
            return SearchStep(
                0.0, turn, "한 바퀴 돌았지만 앞이 막혀 제자리 유지",
                restart_odometry=True,
            )

        self._mode = self.CREEPING
        return SearchStep(
            self.config.creep_speed, 0.0, "한 바퀴 돌았음 — 조금 이동",
            restart_odometry=True,
        )


def front_clearance(
    ranges, angle_min: float, angle_increment: float,
    range_min: float, range_max: float, arc_deg: float = 40.0,
) -> float:
    """정면 부채꼴 안에서 가장 가까운 유효 거리 (m).

    유효값이 하나도 없으면 ``inf`` 를 돌려줍니다 — 라이다가 죽었을 때
    "앞이 막혔다"고 오판해서 영원히 전진을 못 하면 안 되기 때문입니다.
    라이다가 없는 상태의 안전은 이 함수가 아니라 safety_node 의 몫입니다.

    측정 실패값(0.0, inf, nan)은 버립니다. ydlidar 는 못 잰 빔에 0.0 을
    넣는데, 그걸 진짜 거리로 읽으면 항상 막힌 것으로 보입니다.
    """
    if angle_increment == 0.0:
        return math.inf

    half = math.radians(arc_deg) / 2.0
    nearest = math.inf
    for index, distance in enumerate(ranges):
        bearing = angle_min + index * angle_increment
        bearing = math.atan2(math.sin(bearing), math.cos(bearing))
        if abs(bearing) > half:
            continue
        if math.isnan(distance) or math.isinf(distance):
            continue
        if not (range_min < distance < range_max):
            continue
        nearest = min(nearest, distance)
    return nearest
