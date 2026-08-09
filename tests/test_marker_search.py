"""마커 탐색 동작 검증.

실기에서 접근 도중 마커가 화면 밖으로 밀려나면 로봇이 그대로 멈춰
90초를 서 있다가 실패했다. 여기서 잡는 것은 그 재발과, 전진 탐색이
로봇을 벽에 가둬버리는 경우다.
"""

import math

import pytest

from potner_docking.marker_search import (
    MarkerSearch,
    SearchConfig,
    front_clearance,
)

OPEN = 5.0  # 앞이 트인 상태
WALL = 0.30  # front_clear_m(0.50) 보다 가까움
FULL_TURN = math.radians(360.0)


def search(**kwargs):
    return MarkerSearch(SearchConfig(**kwargs)) if kwargs else MarkerSearch()


# --- 회전 ---


def test_처음에는_제자리에서_돈다():
    step = search().step(turned_rad=0.0, crept_m=0.0, front_range_m=OPEN)

    assert step.linear == 0.0
    assert step.angular != 0.0


def test_한_바퀴_돌기_전에는_계속_돈다():
    step = search().step(turned_rad=math.radians(180.0), crept_m=0.0, front_range_m=OPEN)

    assert step.linear == 0.0
    assert step.angular != 0.0


def test_방향을_주면_그쪽으로_돈다():
    """접근 중 놓쳤으면 마지막으로 본 쪽으로 돌아야 헛돌지 않는다."""
    left = search()
    left.reset(direction=1.0)
    right = search()
    right.reset(direction=-1.0)

    assert left.step(0.0, 0.0, OPEN).angular > 0
    assert right.step(0.0, 0.0, OPEN).angular < 0


# --- 전진 ---


def test_한_바퀴_돌고_앞이_트였으면_전진한다():
    finder = search()
    step = finder.step(turned_rad=FULL_TURN, crept_m=0.0, front_range_m=OPEN)

    assert step.linear > 0.0
    assert step.angular == 0.0
    assert step.restart_odometry is True
    assert finder.mode == MarkerSearch.CREEPING


def test_정해진_거리만큼만_전진하고_다시_돈다():
    finder = search()
    finder.step(FULL_TURN, 0.0, OPEN)  # 전진 시작

    keep = finder.step(0.0, 0.05, OPEN)
    assert keep.linear > 0.0

    done = finder.step(0.0, 0.20, OPEN)  # creep_distance(0.15) 초과
    assert done.linear == 0.0
    assert done.angular != 0.0
    assert done.restart_odometry is True
    assert finder.mode == MarkerSearch.ROTATING


# --- 갇힘 방지 ---


def test_앞이_막혔으면_한_바퀴_돌아도_전진하지_않는다():
    """safety 는 우선순위 255 라 걸리는 순간 도킹(150)이 회전조차 못 한다.

    후진도 없어서 스스로 빠져나올 수 없다. 그래서 safety 가 걸리는 거리에
    닿기 전에 이쪽에서 먼저 멈춰야 한다.
    """
    finder = search()
    step = finder.step(turned_rad=FULL_TURN, crept_m=0.0, front_range_m=WALL)

    assert step.linear == 0.0
    assert step.angular != 0.0
    assert finder.mode == MarkerSearch.ROTATING


def test_전진_중에_앞이_막히면_즉시_멈추고_회전으로_바꾼다():
    finder = search()
    finder.step(FULL_TURN, 0.0, OPEN)  # 전진 시작

    step = finder.step(0.0, 0.05, WALL)  # 가다가 벽을 만남

    assert step.linear == 0.0
    assert finder.mode == MarkerSearch.ROTATING


def test_거리를_모르면_전진한다():
    """라이다가 죽었을 때 '막혔다'고 오판해 영원히 전진을 못 하면 안 된다.

    그 상황의 안전은 이 로직이 아니라 safety_node 의 몫이다.
    """
    step = search().step(FULL_TURN, 0.0, math.inf)

    assert step.linear > 0.0


# --- 정면 거리 ---


def _scan(values, angle_min=-math.pi, count=None):
    count = count or len(values)
    return dict(
        ranges=values,
        angle_min=angle_min,
        angle_increment=2.0 * math.pi / count,
        range_min=0.1,
        range_max=12.0,
    )


def test_정면_부채꼴_안에서만_가장_가까운_값을_고른다():
    n = 360
    values = [5.0] * n
    values[n // 2] = 0.4          # 정면 (angle_min=-pi 이므로 한가운데)
    values[0] = 0.1               # 뒤쪽 — 무시돼야 한다

    assert front_clearance(**_scan(values)) == pytest.approx(0.4)


def test_측정_실패값은_거리로_치지_않는다():
    """ydlidar 는 못 잰 빔에 0.0 을 넣는다. 그걸 거리로 읽으면 항상
    막힌 것으로 보여 전진을 영영 못 한다."""
    n = 360
    values = [5.0] * n
    values[n // 2] = 0.0
    values[n // 2 + 1] = float("inf")
    values[n // 2 + 2] = float("nan")

    assert front_clearance(**_scan(values)) == pytest.approx(5.0)


def test_유효값이_하나도_없으면_무한대다():
    assert math.isinf(front_clearance(**_scan([0.0] * 360)))


def test_각도_증가분이_0이면_무한대다():
    """0 으로 나누는 자리다. 죽는 대신 '모름' 으로 물러나야 한다."""
    assert math.isinf(
        front_clearance(
            ranges=[1.0] * 10, angle_min=-math.pi, angle_increment=0.0,
            range_min=0.1, range_max=12.0,
        )
    )
