"""차동 구동 기구학 검증.

바퀴 지름이나 축간거리를 잘못 넣으면 로봇이 지도에서 흘러갑니다.
그 계산이 맞는지 여기서 확인합니다.
"""

import math

import pytest

from potner_base.kinematics import (
    DriveConfig,
    OdometryIntegrator,
    tick_delta,
    twist_to_wheel_speeds,
    wheel_speeds_to_twist,
)

CFG = DriveConfig(wheel_diameter=0.065, wheel_separation=0.20, counts_per_rev=1440)


def test_직진하면_좌우_바퀴_속도가_같다():
    left, right = twist_to_wheel_speeds(0.1, 0.0, CFG)
    assert left == pytest.approx(0.1)
    assert right == pytest.approx(0.1)


def test_제자리_회전하면_좌우가_반대_부호다():
    left, right = twist_to_wheel_speeds(0.0, 1.0, CFG)
    assert left == pytest.approx(-right)
    assert right > 0  # 반시계 방향이면 오른쪽 바퀴가 앞으로


def test_속도_상한을_넘으면_비율을_유지한_채_줄인다():
    cfg = DriveConfig(wheel_separation=0.20, max_wheel_speed=0.25)
    left, right = twist_to_wheel_speeds(1.0, 2.0, cfg)

    assert max(abs(left), abs(right)) == pytest.approx(0.25)
    # 한쪽만 자르면 로봇이 의도치 않은 방향으로 휩니다. 비율이 유지돼야 합니다.
    raw_left, raw_right = 1.0 - 0.2, 1.0 + 0.2
    assert left / right == pytest.approx(raw_left / raw_right)


def test_기구학_변환은_왕복해도_같다():
    left, right = twist_to_wheel_speeds(0.12, 0.4, CFG)
    linear, angular = wheel_speeds_to_twist(left, right, CFG)
    assert linear == pytest.approx(0.12)
    assert angular == pytest.approx(0.4)


def test_엔코더_카운터가_32비트에서_넘어가도_증분이_정상이다():
    """이걸 처리 안 하면 오버플로 순간 로봇이 40억 미터를 순간이동합니다."""
    assert tick_delta(2147483647, -2147483648) == 1
    assert tick_delta(-2147483648, 2147483647) == -1
    assert tick_delta(100, 150) == 50
    assert tick_delta(150, 100) == -50


def test_한_바퀴_굴리면_원주만큼_전진한다():
    odom = OdometryIntegrator(CFG)
    odom.update(0, 0, 0.1)  # 첫 샘플은 기준점만 잡음
    odom.update(1440, 1440, 1.0)  # 양쪽 바퀴 정확히 1회전

    x, y, theta = odom.pose
    assert x == pytest.approx(math.pi * 0.065, abs=1e-6)
    assert y == pytest.approx(0.0, abs=1e-9)
    assert theta == pytest.approx(0.0, abs=1e-9)


def test_한쪽만_굴리면_회전한다():
    odom = OdometryIntegrator(CFG)
    odom.update(0, 0, 0.1)
    odom.update(0, 1440, 1.0)  # 오른쪽만 1회전

    _, _, theta = odom.pose
    expected = (math.pi * 0.065) / CFG.wheel_separation
    assert theta == pytest.approx(expected, abs=1e-6)


def test_첫_샘플은_이동으로_치지_않는다():
    """전원을 켰을 때 엔코더가 0이 아니어도 순간이동하지 않아야 합니다."""
    odom = OdometryIntegrator(CFG)
    odom.update(999999, -555555, 0.1)
    assert odom.pose == (0.0, 0.0, 0.0)
