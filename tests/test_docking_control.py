"""도킹 접근 제어 검증 (legacy/vision/auto_docking_vision.py P제어 이식분)."""

import pytest

from potner_docking.approach_controller import DockingGains, compute, normalize_yaw

GAINS = DockingGains()


def test_거리와_정렬이_모두_맞으면_도킹_완료():
    command = compute(0.14, 5.0, 2.0, GAINS)
    assert command.docked is True
    assert command.linear == 0.0
    assert command.angular == 0.0


def test_멀면_전진한다():
    command = compute(1.0, 0.0, 0.0, GAINS)
    assert command.docked is False
    assert command.linear == pytest.approx(GAINS.approach_speed)


def test_마커가_오른쪽에_있으면_오른쪽으로_돈다():
    command = compute(1.0, 100.0, 0.0, GAINS)
    assert command.angular > 0


def test_마커가_왼쪽에_있으면_왼쪽으로_돈다():
    command = compute(1.0, -100.0, 0.0, GAINS)
    assert command.angular < 0


def test_멀리서는_기울기를_무시한다():
    """원거리에서 기울기까지 보면 제어가 출렁입니다."""
    far = compute(1.0, 0.0, 30.0, GAINS)
    assert far.angular == pytest.approx(0.0)


def test_가까워지면_기울기도_보정한다():
    near = compute(0.30, 0.0, 30.0, GAINS)
    assert near.angular != pytest.approx(0.0)


def test_거리는_됐는데_정렬이_안_되면_제자리에서_돈다():
    """차동 구동이라 가능한 동작입니다. 애커만 차량에서는 안 됐습니다."""
    command = compute(0.14, 100.0, 0.0, GAINS)
    assert command.docked is False
    assert command.linear == 0.0
    assert command.angular != 0.0


def test_각속도에_상한이_걸린다():
    command = compute(1.0, 100000.0, 0.0, GAINS)
    assert abs(command.angular) <= GAINS.max_angular


def test_마커를_정면에서_본_각도를_0_기준으로_접는다():
    assert normalize_yaw(175.0) == pytest.approx(-5.0)
    assert normalize_yaw(-175.0) == pytest.approx(5.0)
    assert normalize_yaw(10.0) == pytest.approx(10.0)
