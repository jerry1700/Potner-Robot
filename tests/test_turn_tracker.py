"""도킹 후 제자리 회전량 누적 검증."""

import math

import pytest

from potner_docking.turn_tracker import (
    TurnAccumulator,
    shortest_angle_diff,
    yaw_from_quaternion,
)


def feed(accumulator, degrees_list):
    """각도(도) 나열을 순서대로 먹입니다."""
    for value in degrees_list:
        accumulator.update(math.radians(value))
    return accumulator


def sweep(start_deg, end_deg, step_deg=5.0):
    """start 에서 end 까지 step 간격으로 도는 yaw 표본을 만듭니다.

    실제 오도메트리처럼 -180~180 으로 접어서 돌려줍니다.
    """
    count = int(abs(end_deg - start_deg) / step_deg)
    sign = 1.0 if end_deg >= start_deg else -1.0
    samples = []
    for index in range(count + 1):
        raw = start_deg + sign * index * step_deg
        samples.append((raw + 180.0) % 360.0 - 180.0)
    return samples


# --- 쿼터니언 ---


@pytest.mark.parametrize("degrees", [0.0, 45.0, 90.0, 179.0, -90.0, -179.0])
def test_쿼터니언에서_yaw를_되찾는다(degrees):
    yaw = math.radians(degrees)
    z, w = math.sin(yaw / 2.0), math.cos(yaw / 2.0)

    assert yaw_from_quaternion(0.0, 0.0, z, w) == pytest.approx(yaw)


# --- 최단각 ---


def test_최단각은_pi_경계를_넘어도_이어진다():
    diff = shortest_angle_diff(math.radians(-179.0), math.radians(179.0))

    assert diff == pytest.approx(math.radians(2.0), abs=1e-9)


# --- 누적 ---


def test_첫_표본은_기준점만_잡는다():
    accumulator = TurnAccumulator()

    assert accumulator.update(math.radians(30.0)) == 0.0
    assert accumulator.started is True


def test_180도_회전을_잰다():
    """시작 yaw 와 현재 yaw 를 한 번에 빼는 방식으로는 못 재는 각도다.

    최단각은 -pi~pi 로 접히므로 180도는 부호조차 정해지지 않는다. 그래서
    연속한 표본 사이의 차를 누적한다.
    """
    accumulator = feed(TurnAccumulator(), sweep(0.0, 180.0))

    assert accumulator.turned == pytest.approx(math.pi, abs=1e-6)


def test_180도를_넘겨도_계속_누적된다():
    accumulator = feed(TurnAccumulator(), sweep(0.0, 270.0))

    assert accumulator.turned == pytest.approx(math.radians(270.0), abs=1e-6)


def test_한_바퀴도_잰다():
    accumulator = feed(TurnAccumulator(), sweep(0.0, 360.0))

    assert accumulator.turned == pytest.approx(2.0 * math.pi, abs=1e-6)


def test_우회전은_음수로_누적되고_크기는_양수다():
    accumulator = feed(TurnAccumulator(), sweep(0.0, -180.0))

    assert accumulator.total == pytest.approx(-math.pi, abs=1e-6)
    assert accumulator.turned == pytest.approx(math.pi, abs=1e-6)


def test_왔다갔다하면_알짜_회전만_남는다():
    """제자리에서 흔들린 것을 회전으로 세면 목표를 일찍 채운다."""
    accumulator = feed(TurnAccumulator(), [0.0, 30.0, 0.0, 30.0, 0.0])

    assert accumulator.turned == pytest.approx(0.0, abs=1e-9)


def test_시작_yaw가_pi_근처여도_맞다():
    """도킹 방향에 따라 회전이 어느 yaw 에서 시작될지 모른다."""
    accumulator = feed(TurnAccumulator(), sweep(170.0, 350.0))

    assert accumulator.turned == pytest.approx(math.pi, abs=1e-6)


def test_reset하면_처음부터_다시_센다():
    accumulator = feed(TurnAccumulator(), sweep(0.0, 90.0))
    accumulator.reset()

    assert accumulator.started is False
    assert accumulator.turned == 0.0

    feed(accumulator, sweep(0.0, 45.0))

    assert accumulator.turned == pytest.approx(math.radians(45.0), abs=1e-6)
