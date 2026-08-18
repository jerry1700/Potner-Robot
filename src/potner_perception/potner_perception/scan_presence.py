"""정지한 로봇의 라이다 스캔에서 "없던 것이 나타났다"를 판정하는 순수 로직.

ROS 에 의존하지 않으므로 CI 에서 검증됩니다.

왜 카메라가 아니라 라이다인가 — 카메라가 바닥에서 13cm 에 **틸트 없이**
달려 있습니다(`potner.urdf.xacro`). 세로 화각 30.6도라 1m 앞 사람은
바닥~40cm 만 프레임에 들어오고, 성인 전신이 담기려면 5.73m 떨어져야
합니다. 그 거리에서 얼굴 폭은 22.9px 로, OpenCV Haar 의 학습창 24px 보다
작습니다. **어느 거리에도 쓸 수 있는 얼굴이 존재하지 않습니다.** 카메라를
위로 기울이는 것도 막혀 있습니다 — 2m 에서 턱을 넣으려면 18.58도 이상
올려야 하는데 ArUco 도킹이 견디는 상한이 17.11도라 교집합이 없습니다.
그 카메라는 13cm 높이 마커를 보라고 놓은 자리입니다.

반면 라이다는 바닥 52cm, 성인 허벅지 높이라 다리가 또렷하게 찍힙니다.
어두워도 되는 것은 덤입니다 — 귀가는 대개 밤입니다.

**정확도가 낮아도 되는 이유가 있습니다.** 서버의 귀가 알림(GPS 지오펜스)이
인사를 무장시키고, 무장된 시간창 동안 현관에서만 이 판정을 씁니다
(`potner_mission/greeting.py`). 그 좁은 조건 안에서 다리 높이에 나타난
물체는 주인입니다. "사람인지"를 가릴 필요 없이 "배경에 없던 것이
생겼는지"만 보면 됩니다.

**대신 오탐이 비쌉니다.** `mission_manager._on_person` 은 인사를 시작하면서
`mark_greeted()` 로 시간창을 닫고 같은 콜백에서 `_begin_return_home()` 까지
부릅니다. 오탐 한 번이 인사 기회를 태우는 데 그치지 않고 **로봇을 문
앞에서 떠나보냅니다.** 재시도가 없습니다. 그래서 임계값은 놓치는 쪽으로
기울입니다 — 못 잡으면 대기하다 돌아가지만, 잘못 잡으면 주인이 오기 전에
가버립니다.

★ 배경(baseline)은 **로봇이 멈춰 있을 때만** 유효합니다. 움직이며 잡은
  배경은 다음 순간 전부 어긋나 모든 빔이 침입으로 잡힙니다. 그래서 정지
  판정을 이 모듈 안에 넣었습니다 — "준비 안 된 상태에서 절대 참을 내지
  않는다"를 rclpy 없이 검증할 수 있어야 하기 때문입니다.
"""

import math
from dataclasses import dataclass

# 성인 다리 한 짝의 폭 가정. 스캔 평면(바닥 52cm)이 허벅지를 지납니다.
LEG_WIDTH_M = 0.12


@dataclass(frozen=True)
class PresenceConfig:
    """판정 임계값.

    ``potner_params.yaml`` 의 ``scan_presence`` 블록, 그리고 노드의
    ``declare_parameter`` 기본값과 **세 곳이 같아야** 합니다.
    ``tests/test_config_consistency.py`` 가 셋을 묶어 대조합니다.
    """

    # 정면 부채꼴 폭. 카메라 화각 40.1도보다 넓게 두되 360도는 아닙니다 —
    # 뒤쪽을 지나가는 사람에게 인사하면 안 됩니다.
    sector_deg: float = 100.0
    # safety 의 해제선(scan_stop_distance + clear_hysteresis) 바깥에서만
    # 봅니다. 그 안쪽은 비상정지가 걸린 구역이라, 사람을 인식하는 순간이 곧
    # 로봇이 못 움직이는 순간이 됩니다. 두 값이 어긋나지 않는지는
    # tests/test_config_consistency.py 가 yaml 을 읽어 확인합니다.
    min_range_m: float = 0.40
    max_range_m: float = 2.50
    # 배경보다 이만큼은 가까워져야 "새로 생긴 것"입니다. 라이다 잡음과
    # 배경의 미세한 흔들림(커튼, 화분 잎)을 걸러냅니다.
    baseline_margin_m: float = 0.12
    leg_width_m: float = 0.12
    # 기대 각폭의 이 배율 ~ 그 역수 배율까지 사람으로 인정합니다
    # (0.5 이면 0.5배~2배). 코트나 가방이 있으면 폭이 커집니다.
    width_tolerance: float = 0.5
    # 연속 이만큼 보여야 확정. 11.4Hz 에서 3스캔이면 263ms 라 체감되지
    # 않으면서 단발 잡음은 걸러집니다.
    persist_scans: int = 3
    baseline_scans: int = 10
    # 정지 후 배경을 잡기까지 기다리는 시간. Nav2 도착 오차와 캐스터가
    # 자리를 잡는 시간입니다.
    settle_seconds: float = 1.5
    motion_epsilon: float = 0.02


@dataclass(frozen=True)
class ScanFrame:
    """LaserScan 에서 판정에 필요한 것만 뽑아낸 것."""

    ranges: list
    angle_min: float
    angle_increment: float
    range_min: float
    range_max: float


@dataclass(frozen=True)
class Intrusion:
    """배경에 없던 물체 하나."""

    start_index: int
    end_index: int
    point_count: int
    distance_m: float


def sanitize_ranges(frame: ScanFrame) -> list:
    """측정 실패값을 ``None`` 으로 바꿉니다.

    ``ydlidar.yaml`` 이 ``invalid_range_is_inf: false`` 라서 실패값이
    **0.0 으로** 옵니다. 0.0 을 진짜 거리로 읽으면 코앞에 물체가 있는
    것으로 오판합니다 — `safety_node` 가 같은 이유로 같은 검사를 하고,
    그 검사가 없어서 로봇이 영영 못 움직였던 적이 있습니다. inf 로 오는
    설정으로 바뀌어도 걸러지도록 둘 다 막습니다.
    """
    clean = []
    for distance in frame.ranges:
        if (
            distance is None
            or math.isnan(distance)
            or math.isinf(distance)
            or not (frame.range_min < distance < frame.range_max)
        ):
            clean.append(None)
        else:
            clean.append(distance)
    return clean


def sector_bounds(frame: ScanFrame, sector_deg: float) -> tuple:
    """정면 부채꼴에 해당하는 빔 번호 구간 ``[시작, 끝)`` 을 돌려줍니다.

    ``angle_increment`` 가 0 이면 빈 구간을 돌려줍니다 — 0 으로 나누는
    자리라서, 예외를 던지는 대신 "아무것도 못 봄"으로 물러납니다.
    """
    if frame.angle_increment == 0.0:
        return (0, 0)

    half = math.radians(sector_deg) / 2.0
    start = None
    end = 0
    for index in range(len(frame.ranges)):
        bearing = frame.angle_min + index * frame.angle_increment
        bearing = math.atan2(math.sin(bearing), math.cos(bearing))
        if abs(bearing) <= half:
            if start is None:
                start = index
            end = index + 1
    return (0, 0) if start is None else (start, end)


def merge_baseline(baseline, sample) -> list:
    """배경을 빔별 **가장 먼 유효값**으로 갱신합니다.

    평균이나 중앙값이 아닌 이유가 있습니다. 배경을 잡는 동안 누가 앞을
    지나가면 그 빔의 표본 절반이 가까운 값이 되어 중앙값이 끌려옵니다.
    그러면 그 빔은 이후 사람이 서 있어도 침입으로 안 잡힙니다. "아무도
    없을 때 이 빔이 볼 수 있는 가장 먼 곳"이 배경의 정의라, 최대값이
    지나가는 물체에 오염되지 않습니다.

    한 번도 유효하지 않았던 빔은 ``None`` 으로 남습니다.
    """
    if not baseline:
        return list(sample)

    merged = []
    for old, new in zip(baseline, sample):
        if new is None:
            merged.append(old)
        elif old is None:
            merged.append(new)
        else:
            merged.append(max(old, new))
    return merged


def expected_leg_points(
    distance_m: float, angle_increment: float, leg_width_m: float = LEG_WIDTH_M
) -> float:
    """그 거리의 다리 하나가 몇 개 빔에 찍힐지.

    고정된 빔 개수 창(예: 4~15개)을 쓰면 안 됩니다. 각폭이 거리에 반비례해서
    0.4m 에서 20.8개, 1.0m 에서 8.4개, 2.5m 에서 3.35개로 6배 넘게 변합니다
    (0.82도/빔 기준). 고정 창은 가까운 진짜 다리와 먼 진짜 다리를 양쪽에서
    버립니다.
    """
    if angle_increment == 0.0 or distance_m <= 0.0:
        return 0.0
    span = 2.0 * math.atan2(leg_width_m / 2.0, distance_m)
    return span / abs(angle_increment)


def find_intrusions(baseline, frame: ScanFrame, config: PresenceConfig) -> list:
    """이번 스캔에서 배경에 없던 물체들을 찾습니다.

    상태를 바꾸지 않는 순수 함수입니다 — 판단과 기록을 나눈
    ``GreetingPolicy.should_greet`` / ``mark_greeted`` 와 같은 규약입니다.
    """
    ranges = sanitize_ranges(frame)
    start, end = sector_bounds(frame, config.sector_deg)

    hits = []
    for index in range(start, min(end, len(baseline))):
        distance = ranges[index]
        if distance is None:
            continue
        if not (config.min_range_m <= distance <= config.max_range_m):
            continue
        background = baseline[index]
        # 한 번도 유효하지 않았던 빔은 배경을 모르는 것이라 판단하지
        # 않습니다. 여기서 inf 처럼 다루면 창문 쪽 빔이 전부 침입이 됩니다.
        if background is None:
            continue
        if background - distance < config.baseline_margin_m:
            continue
        hits.append(index)

    return _accept_runs(hits, ranges, frame, config)


def _accept_runs(hits, ranges, frame, config):
    found = []
    for run in _group_consecutive(hits):
        distances = sorted(ranges[i] for i in run)
        distance = distances[len(distances) // 2]

        expected = expected_leg_points(
            distance, frame.angle_increment, config.leg_width_m
        )
        if expected <= 0.0:
            continue
        # 빔 두 개는 절대 하한입니다. 먼 거리에서 기대치가 작아져도
        # 한 점짜리 잡음이 통과하면 안 됩니다.
        lowest = max(2.0, expected * config.width_tolerance)
        highest = expected / config.width_tolerance
        if not (lowest <= len(run) <= highest):
            continue

        found.append(
            Intrusion(
                start_index=run[0],
                end_index=run[-1],
                point_count=len(run),
                distance_m=distance,
            )
        )
    return found


def _group_consecutive(indices):
    if not indices:
        return []
    runs = [[indices[0]]]
    for index in indices[1:]:
        if index - runs[-1][-1] == 1:
            runs[-1].append(index)
        else:
            runs.append([index])
    return runs


class ScanPresenceDetector:
    """스캔을 먹여 넣으면 "사람이 있다/없다"를 돌려줍니다.

    정지 판정과 배경 수집까지 여기서 다룹니다. 노드는 시각과 스캔을
    넘겨주기만 합니다.
    """

    def __init__(self, config: PresenceConfig = None):
        self.config = config or PresenceConfig()
        self._baseline = []
        self._samples = 0
        self._hits = 0
        self._last_moving_at = None
        self._first_feed_at = None

    @property
    def ready(self) -> bool:
        """배경 수집이 끝났는지."""
        return self._samples >= self.config.baseline_scans

    @property
    def baseline(self) -> list:
        return list(self._baseline)

    def reset(self) -> None:
        """배경을 버리고 처음부터 다시 모읍니다."""
        self._baseline = []
        self._samples = 0
        self._hits = 0

    def note_motion(self, now: float, speed: float) -> None:
        """주행 명령의 크기를 알려줍니다.

        움직이는 순간 배경을 버립니다. 움직이며 잡은 배경은 다음 순간
        전부 어긋나서 모든 빔이 침입으로 잡히고, 그러면 오탐 한 번에
        인사가 소모됩니다.
        """
        if abs(speed) > self.config.motion_epsilon:
            self._last_moving_at = now
            self.reset()

    def settled(self, now: float) -> bool:
        """정지한 지 ``settle_seconds`` 가 지났는지."""
        if self._first_feed_at is None:
            return False
        if now - self._first_feed_at < self.config.settle_seconds:
            return False
        if self._last_moving_at is None:
            return True
        return now - self._last_moving_at >= self.config.settle_seconds

    def feed(self, now: float, frame: ScanFrame) -> bool:
        """스캔 하나를 반영하고 현재 판정을 돌려줍니다."""
        if self._first_feed_at is None:
            self._first_feed_at = now

        if not self.settled(now):
            self._hits = 0
            return False

        if not self.ready:
            self._baseline = merge_baseline(self._baseline, sanitize_ranges(frame))
            self._samples += 1
            self._hits = 0
            return False

        if find_intrusions(self._baseline, frame, self.config):
            self._hits += 1
        else:
            self._hits = 0

        return self._hits >= self.config.persist_scans
