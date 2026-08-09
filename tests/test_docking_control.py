"""도킹 접근 제어 검증 (legacy/vision/auto_docking_vision.py P제어 이식분)."""

import pytest

from potner_docking.approach_controller import DockingGains, compute

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
    """angular.z 는 REP-103 대로 **왼쪽(CCW)이 +** 다.

    그래서 "오른쪽으로 돈다" 는 angular < 0 이다. 예전에는 이 단언이
    `> 0` 으로 적혀 있어서, 이름은 맞는데 값이 반대인 채로 CI 를
    통과했다. 그 사이 실기에서는 제어가 마커를 화면 밖으로 밀어냈다.
    """
    command = compute(1.0, 100.0, 0.0, GAINS)
    assert command.angular < 0


def test_마커가_왼쪽에_있으면_왼쪽으로_돈다():
    command = compute(1.0, -100.0, 0.0, GAINS)
    assert command.angular > 0


def test_횡방향_보정은_음의_되먹임이다():
    """오차가 커지면 반대 방향으로 더 세게 돌아야 한다.

    부호가 뒤집히면 이득을 아무리 낮춰도 발산만 느려질 뿐이라, 게인
    튜닝으로는 절대 못 고친다. 그래서 부호 자체를 시험으로 못 박는다.
    """
    small = compute(1.0, 50.0, 0.0, GAINS).angular
    large = compute(1.0, 150.0, 0.0, GAINS).angular

    assert small < 0 and large < 0
    assert large < small  # 오차가 클수록 더 세게(음수로 더 크게) 돈다


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
