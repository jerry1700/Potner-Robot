"""수동 주행 한 스텝의 목표량 판정 로직 검증."""

import math

import pytest

from potner_mission.drive_progress import (
    shortest_angle_diff,
    straight_progress,
    turn_progress,
    yaw_from_quaternion,
)


# --- 쿼터니언 -> yaw ---


@pytest.mark.parametrize("degrees", [0.0, 22.5, 90.0, 179.0, -22.5, -90.0, -179.0])
def test_평면_회전_쿼터니언에서_yaw를_되찾는다(degrees):
    """base_driver 가 yaw 를 z/w 만 채워 발행하므로 그 역변환이 맞아야 한다."""
    yaw = math.radians(degrees)
    z, w = math.sin(yaw / 2.0), math.cos(yaw / 2.0)

    assert yaw_from_quaternion(0.0, 0.0, z, w) == pytest.approx(yaw)


# --- 최단 각도 차 ---


def test_각도_차는_pi_경계를_넘어도_최단으로_잰다():
    """단순 뺄셈을 쓰면 179도에서 -179도로 갈 때 358도가 나와, 회전이 끝난
    것으로 오판하고 즉시 멈춘다."""
    diff = shortest_angle_diff(math.radians(-179.0), math.radians(179.0))

    assert diff == pytest.approx(math.radians(2.0), abs=1e-9)


# --- 회전 진행량 ---


@pytest.mark.parametrize("delta_deg", [22.5, -22.5, 90.0, -90.0])
def test_회전_진행량은_방향과_무관하게_양수다(delta_deg):
    """좌회전과 우회전이 같은 목표값과 비교되어야 한다."""
    start = math.radians(10.0)
    current = start + math.radians(delta_deg)

    assert turn_progress(start, current) == pytest.approx(
        abs(math.radians(delta_deg))
    )


def test_회전_진행량은_시작_각도가_pi_근처여도_맞다():
    start = math.radians(170.0)
    current = math.radians(-170.0)  # 20도 더 돈 것

    assert turn_progress(start, current) == pytest.approx(
        math.radians(20.0), abs=1e-9
    )


def test_22_5도_목표는_그_전에_도달로_판정하지_않는다():
    """실기에서 시간으로 끊었을 때 29.9도까지 돌았다. 판정이 목표 직전에
    참이 되면 같은 오버슈트가 재현된다."""
    goal = math.radians(22.5)
    start = 0.0

    assert turn_progress(start, math.radians(22.0)) < goal
    assert turn_progress(start, math.radians(22.5)) >= goal


# --- 직진 진행량 ---


def test_직진_진행량은_시작점에서의_직선거리다():
    assert straight_progress(1.0, 2.0, 1.15, 2.0) == pytest.approx(0.15)


def test_후진도_양수로_잰다():
    """전진과 후진이 같은 목표값과 비교되어야 한다."""
    assert straight_progress(0.0, 0.0, -0.15, 0.0) == pytest.approx(0.15)


def test_제자리_회전은_직진_진행량을_거의_만들지_않는다():
    """실기 회전 로그에서 이동량이 0.001m 였다. 회전을 거리로 판정하면
    목표를 영원히 못 채워 시한까지 돈다."""
    assert straight_progress(0.0, 0.0, 0.001, 0.0) < 0.15
