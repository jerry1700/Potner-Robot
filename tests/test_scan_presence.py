"""라이다 기반 사람 감지 판정 검증.

실기에서 이 프로젝트를 실제로 물었던 함정 위주로 짰습니다 — 측정 실패값을
진짜 거리로 읽는 것, 정지 전에 배경을 잡는 것, 거리에 따라 변하는 각폭을
고정 창으로 자르는 것.
"""

import math

import pytest

from potner_perception.scan_presence import (
    Intrusion,
    PresenceConfig,
    ScanFrame,
    ScanPresenceDetector,
    expected_leg_points,
    find_intrusions,
    merge_baseline,
    sanitize_ranges,
    sector_bounds,
)

# X4 Pro 실측 메모(5kHz 표본 / 11.4Hz 회전)에서 나온 한 바퀴 빔 수.
BEAMS = 438
INCREMENT = 2.0 * math.pi / BEAMS
FRONT = BEAMS // 2  # angle_min 이 -pi 라 정면은 한가운데
EMPTY_DISTANCE = 5.0


def frame(ranges=None):
    return ScanFrame(
        ranges=list(ranges) if ranges is not None else [EMPTY_DISTANCE] * BEAMS,
        angle_min=-math.pi,
        angle_increment=INCREMENT,
        range_min=0.1,
        range_max=12.0,
    )


def with_object(distance, beams=None, center=FRONT, base=None):
    """정면 근처에 물체 하나를 놓은 스캔을 만듭니다.

    ``beams`` 를 안 주면 **그 거리의 다리 하나에 해당하는 실제 빔 수**를
    씁니다. 고정값을 쓰면 시험이 물리와 어긋나서, 0.5m 에 8개짜리 물체를
    놓고 "사람인데 왜 안 잡히지" 하게 됩니다 — 0.5m 의 다리는 17개입니다.
    """
    if beams is None:
        beams = max(2, round(expected_leg_points(distance, INCREMENT)))
    values = list(base) if base is not None else [EMPTY_DISTANCE] * BEAMS
    start = center - beams // 2
    for index in range(start, start + beams):
        values[index] = distance
    return frame(values)


def warmed_detector(config=None):
    """배경 수집까지 끝낸 감지기와 그때의 시각을 돌려줍니다."""
    detector = ScanPresenceDetector(config)
    detector.feed(0.0, frame())  # 첫 호출로 settle 타이머 시작
    now = 2.0
    while not detector.ready:
        detector.feed(now, frame())
        now += 0.1
    return detector, now


# --- 측정 실패값 ---


@pytest.mark.parametrize("bad", [0.0, math.inf, math.nan, 20.0])
def test_무효값은_배경에도_침입에도_쓰이지_않는다(bad):
    """ydlidar.yaml 이 invalid_range_is_inf: false 라 실패값이 0.0 으로 온다.

    safety_node 가 0 을 진짜 거리로 읽어 로봇이 영영 못 움직였던 적이 있다.
    설정이 inf 로 바뀌어도 걸러져야 한다.
    """
    clean = sanitize_ranges(frame([bad] * BEAMS))

    assert all(value is None for value in clean)


def test_한_번도_유효하지_않았던_광선은_침입_판정에서_제외한다():
    """창문 쪽 빔은 배경이 없다. 이걸 inf 처럼 다루면 전부 침입이 된다."""
    baseline = [None] * BEAMS

    assert find_intrusions(baseline, with_object(1.0), PresenceConfig()) == []


# --- 침입 판정 ---


def test_배경보다_가까워진_구간만_침입으로_센다():
    baseline = [EMPTY_DISTANCE] * BEAMS

    found = find_intrusions(baseline, with_object(1.0), PresenceConfig())

    assert len(found) == 1
    assert found[0].distance_m == pytest.approx(1.0)


def test_배경보다_멀어진_변화는_사람이_아니다():
    """문이 열려 배경이 사라지면 거리가 멀어진다. 그건 사람이 아니다.

    거리·각폭은 전부 통과하고 '가까워졌는가' 하나만 어긋나게 두었다.
    """
    baseline = [2.0] * BEAMS

    assert find_intrusions(baseline, with_object(2.2), PresenceConfig()) == []


def test_부채꼴_밖의_변화는_무시한다():
    """뒤를 지나가는 사람에게 인사하면 안 된다."""
    baseline = [EMPTY_DISTANCE] * BEAMS
    behind = with_object(1.0, center=5)  # angle_min 근처 = 로봇 뒤쪽

    assert find_intrusions(baseline, behind, PresenceConfig()) == []


def test_하한보다_가까운_것은_무시한다():
    """min_range_m 안쪽은 safety 가 이미 막고 있는 구역이다.

    거기서 사람을 인식해봐야 그 순간이 곧 로봇이 못 움직이는 순간이다.
    (하한이 safety 해제선 바깥인지는 test_config_consistency 가 확인한다.)
    """
    baseline = [EMPTY_DISTANCE] * BEAMS

    assert find_intrusions(baseline, with_object(0.30), PresenceConfig()) == []
    assert find_intrusions(baseline, with_object(0.50), PresenceConfig()) != []


def test_먼_거리의_물체도_무시한다():
    baseline = [EMPTY_DISTANCE] * BEAMS

    assert find_intrusions(baseline, with_object(4.0), PresenceConfig()) == []


# --- 각폭 ---


def test_기대_각폭은_거리에_따라_달라진다():
    """고정된 빔 개수 창을 쓰면 안 되는 이유.

    같은 다리가 0.4m 에서 20.8개, 2.5m 에서 3.35개로 6배 넘게 변한다.
    4~15개 같은 고정 창은 가까운 다리와 먼 다리를 양쪽에서 버린다.
    """
    increment = math.radians(0.8208)

    assert expected_leg_points(0.4, increment) == pytest.approx(20.8, abs=0.2)
    assert expected_leg_points(1.0, increment) == pytest.approx(8.4, abs=0.2)
    assert expected_leg_points(2.5, increment) == pytest.approx(3.35, abs=0.2)


def test_한_점짜리_잡음은_사람으로_보지_않는다():
    baseline = [EMPTY_DISTANCE] * BEAMS

    assert find_intrusions(baseline, with_object(1.0, beams=1), PresenceConfig()) == []


def test_벽처럼_너무_넓은_것은_사람으로_보지_않는다():
    baseline = [EMPTY_DISTANCE] * BEAMS

    assert find_intrusions(baseline, with_object(1.0, beams=90), PresenceConfig()) == []


def test_각도_증가분이_0이면_예외_없이_거짓을_낸다():
    """0 으로 나누는 자리다. 죽는 대신 '아무것도 못 봄' 으로 물러나야 한다."""
    broken = ScanFrame(
        ranges=[1.0] * BEAMS,
        angle_min=-math.pi,
        angle_increment=0.0,
        range_min=0.1,
        range_max=12.0,
    )

    assert sector_bounds(broken, 100.0) == (0, 0)
    assert find_intrusions([EMPTY_DISTANCE] * BEAMS, broken, PresenceConfig()) == []


# --- 배경 ---


def test_배경은_가장_먼_유효값으로_갱신된다():
    """배경 잡는 동안 누가 지나가도 오염되지 않아야 한다.

    중앙값이면 표본 절반이 가까운 값일 때 끌려가고, 그러면 그 빔은 이후
    사람이 서 있어도 침입으로 안 잡힌다.
    """
    baseline = merge_baseline([], [3.0, 3.0, 3.0])
    baseline = merge_baseline(baseline, [1.0, 5.0, None])

    assert baseline == [3.0, 5.0, 3.0]


def test_배경이_없던_빔은_새_유효값으로_채워진다():
    assert merge_baseline([None, 2.0], [4.0, None]) == [4.0, 2.0]


# --- 시간 판정 ---


def test_배경_수집_전에는_어떤_경우에도_참을_내지_않는다():
    detector = ScanPresenceDetector()
    detector.feed(0.0, frame())

    assert detector.feed(2.0, with_object(1.0)) is False
    assert detector.ready is False


def test_정지_직후_settle_시간_전에는_배경을_잡지_않는다():
    """Nav2 도착 오차와 캐스터가 자리를 잡기 전에 배경을 잡으면 어긋난다."""
    detector = ScanPresenceDetector()

    detector.feed(0.0, frame())
    detector.feed(1.0, frame())  # settle_seconds 1.5 이전

    assert detector.settled(1.0) is False
    assert detector.baseline == []


def test_한_스캔만_튄_점은_사람으로_판정하지_않는다():
    detector, now = warmed_detector()

    assert detector.feed(now, with_object(1.0)) is False


def test_연속_세_스캔_지속되면_사람으로_판정한다():
    detector, now = warmed_detector()

    results = [detector.feed(now + i * 0.1, with_object(1.0)) for i in range(3)]

    assert results == [False, False, True]


def test_사이에_한_번_끊기면_처음부터_다시_센다():
    detector, now = warmed_detector()

    detector.feed(now, with_object(1.0))
    detector.feed(now + 0.1, with_object(1.0))
    detector.feed(now + 0.2, frame())  # 끊김
    detector.feed(now + 0.3, with_object(1.0))

    assert detector.feed(now + 0.4, with_object(1.0)) is False


def test_주행_중에는_배경이_폐기되고_참을_내지_않는다():
    """움직이며 잡은 배경은 다음 순간 전부 어긋나 모든 빔이 침입이 된다."""
    detector, now = warmed_detector()
    assert detector.ready is True

    detector.note_motion(now, 0.12)

    assert detector.ready is False
    assert detector.feed(now + 0.1, with_object(1.0)) is False


def test_아주_작은_속도는_주행으로_보지_않는다():
    """twist_mux 가 흘리는 0 근처 값에 배경을 매번 버리면 영영 못 잡는다."""
    detector, now = warmed_detector()

    detector.note_motion(now, 0.001)

    assert detector.ready is True


# --- 규약 ---


def test_같은_사람이_머물러도_판정_함수는_상태를_바꾸지_않는다():
    """판단과 기록의 분리 — greeting.should_greet / mark_greeted 와 같은 규약."""
    baseline = [EMPTY_DISTANCE] * BEAMS
    scan = with_object(1.0)
    config = PresenceConfig()

    first = find_intrusions(baseline, scan, config)
    second = find_intrusions(baseline, scan, config)

    assert first == second
    assert baseline == [EMPTY_DISTANCE] * BEAMS


def test_침입_결과는_불변이다():
    intrusion = Intrusion(
        start_index=1, end_index=8, point_count=8, distance_m=1.0
    )

    with pytest.raises(AttributeError):
        intrusion.distance_m = 2.0
