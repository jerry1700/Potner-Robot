"""Nav2 없이 두 좌표 사이를 주행하는 제어 검증.

실기에서 확인하기 비싼 것들을 여기서 잡는다 — 목표를 지나쳐 되돌아가며
떠는 경우, 뒤에 있는 목표로 후진해 버리는 경우, 방향 보정이 양의
되먹임이 되어 빙빙 도는 경우.
"""

import math

import pytest

from potner_mission.goto_controller import (
    GotoConfig,
    GotoNavigator,
    GotoPhase,
    Pose,
    shortest_angle_diff,
)

ORIGIN = Pose(0.0, 0.0, 0.0)


def nav(**kwargs):
    return GotoNavigator(GotoConfig(**kwargs)) if kwargs else GotoNavigator()


def goal_within_reach(yaw_deg=0.0):
    """도착 여유 **안쪽**에 있는 목표.

    도착 여유는 도킹 출발 자세를 좌우해서 앞으로도 조정될 값이다.
    거리를 박아 두면 조정할 때마다 이 파일이 깨지므로 여유에서 계산한다.
    """
    return Pose(GotoConfig().position_tolerance * 0.5, 0.0, math.radians(yaw_deg))


def goal_out_of_reach(yaw_deg=0.0):
    """도착 여유 밖에 있는 목표."""
    return Pose(GotoConfig().position_tolerance * 3.0, 0.0, math.radians(yaw_deg))


# --- 주행 ---


def test_정면에_있는_목표로는_전속으로_간다():
    step = nav().step(ORIGIN, Pose(2.0, 0.0, 0.0))

    assert step.phase is GotoPhase.DRIVING
    assert step.linear == pytest.approx(GotoConfig().cruise_speed)
    assert step.angular == pytest.approx(0.0)


def test_목표가_왼쪽에_있으면_왼쪽으로_돈다():
    """angular.z 는 REP-103 대로 왼쪽(CCW)이 + 다.

    도킹 제어기에서 이 부호가 뒤집힌 채 CI 를 통과한 전례가 있어서,
    여기서도 부호 자체를 못 박는다.
    """
    left = nav().step(ORIGIN, Pose(1.0, 1.0, 0.0))
    right = nav().step(ORIGIN, Pose(1.0, -1.0, 0.0))

    assert left.angular > 0
    assert right.angular < 0


def test_방향_보정은_음의_되먹임이다():
    """부호가 뒤집히면 이득을 낮춰도 발산만 느려질 뿐이라 튜닝으로는
    못 고친다."""
    small = nav().step(ORIGIN, Pose(1.0, 0.2, 0.0)).angular
    large = nav().step(ORIGIN, Pose(1.0, 1.0, 0.0)).angular

    assert 0 < small < large


def test_목표가_뒤에_있으면_후진하지_않고_제자리에서_돈다():
    """후진하면 라이다 사각지대로 들어간다. 차동 구동이라 돌면 된다."""
    step = nav().step(ORIGIN, Pose(-2.0, 0.0, 0.0))

    assert step.linear == pytest.approx(0.0)
    assert step.angular != 0.0


def test_옆을_보고_있으면_전진을_거의_안_한다():
    """방향이 많이 틀어진 채 전진하면 크게 휘어 돌아간다."""
    step = nav().step(Pose(0.0, 0.0, math.radians(80.0)), Pose(2.0, 0.0, 0.0))

    assert step.linear < 0.25 * GotoConfig().cruise_speed
    assert step.angular < 0  # 오른쪽으로 되돌아와야 한다


def test_각속도에_상한이_걸린다():
    step = nav().step(Pose(0.0, 0.0, math.pi), Pose(1.0, 0.01, 0.0))

    assert abs(step.angular) <= GotoConfig().max_angular


# --- 감속과 도착 ---


def test_목표에_가까워지면_감속한다():
    """정지 명령이 물리 정지가 되기까지 0.2~0.5초가 걸린다. 전속으로
    목표를 지나면 그 지연만큼 지나친다 — 0.18m/s 면 5~9cm."""
    far = nav().step(ORIGIN, Pose(2.0, 0.0, 0.0))
    near = nav().step(ORIGIN, Pose(0.25, 0.0, 0.0))

    assert near.linear < far.linear
    assert near.linear > 0.0


def test_감속해도_바닥_속도_아래로는_안_내려간다():
    """너무 느리면 바퀴가 정지마찰을 못 이겨 목표 직전에 멈춰 선다."""
    step = nav().step(ORIGIN, goal_out_of_reach())

    assert step.linear >= GotoConfig().min_speed


def test_감속_바닥이_엉뚱한_방향으로_밀지_않는다():
    """목표가 뒤에 있는데 바닥 속도가 붙으면 반대로 밀고 간다."""
    behind = goal_out_of_reach()
    step = nav().step(ORIGIN, Pose(-behind.x, 0.0, 0.0))

    assert step.linear == pytest.approx(0.0)


def test_지점에_닿으면_목표_방향으로_돈다():
    step = nav().step(ORIGIN, goal_within_reach(90.0))

    assert step.phase is GotoPhase.FINAL_TURN
    assert step.linear == 0.0
    assert step.angular > 0  # 왼쪽으로 90도


def test_방향까지_맞으면_도착이다():
    step = nav().step(ORIGIN, goal_within_reach(3.0))

    assert step.phase is GotoPhase.ARRIVED
    assert step.arrived is True
    assert step.linear == 0.0
    assert step.angular == 0.0


def test_마지막_회전도_끝에서_감속한다():
    far = nav().step(ORIGIN, goal_within_reach(120.0))
    near = nav().step(ORIGIN, goal_within_reach(20.0))

    assert 0 < near.angular < far.angular
    assert near.angular >= GotoConfig().turn_min_speed


def test_한_번_도착하면_다시_주행으로_안_돌아간다():
    """목표 코앞에서 위치 오차가 여유를 들락날락하면 "가다 서다"를
    반복하는데, 그 사이 방향이 계속 바뀌어 영영 안 끝난다."""
    navigator = nav()
    navigator.step(ORIGIN, goal_within_reach(90.0))  # 도착

    # 관성으로 조금 밀려나 여유 밖으로 나갔다
    step = navigator.step(ORIGIN, goal_out_of_reach(90.0))

    assert step.phase is GotoPhase.FINAL_TURN
    assert step.linear == 0.0


def test_리셋하면_다시_주행한다():
    navigator = nav()
    navigator.step(ORIGIN, goal_within_reach())
    assert navigator.reached_position is True

    navigator.reset()
    step = navigator.step(ORIGIN, Pose(2.0, 0.0, 0.0))

    assert navigator.reached_position is False
    assert step.phase is GotoPhase.DRIVING


# --- 각도 계산 ---


@pytest.mark.parametrize(
    "target, source, expected",
    [
        (0.0, 0.0, 0.0),
        (math.radians(10.0), math.radians(350.0), math.radians(20.0)),
        (math.radians(350.0), math.radians(10.0), math.radians(-20.0)),
        (math.pi, 0.0, math.pi),
    ],
)
def test_최단각으로_돈다(target, source, expected):
    """350도에서 10도로 갈 때 20도만 돌아야지 340도를 돌면 안 된다."""
    assert shortest_angle_diff(target, source) == pytest.approx(
        expected, abs=1e-9
    )


def test_보고하는_거리와_각도가_실제와_맞는다():
    """액션 피드백으로 나가는 값이라 틀리면 사람이 오판한다."""
    step = nav().step(Pose(1.0, 1.0, 0.0), Pose(4.0, 5.0, math.radians(30.0)))

    assert step.distance == pytest.approx(5.0)  # 3-4-5 삼각형
    assert step.yaw_error == pytest.approx(math.radians(30.0))
