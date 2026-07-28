"""도킹 세션 상태 전이 검증.

시간을 인자로 받는 구조라 "3초간 마커를 놓쳤을 때" 같은 시나리오를
실제로 기다리지 않고 검증합니다. 실기에서 재현하기 어렵고 실패하면
스테이션을 들이받는 경우들을 여기서 잡습니다.
"""

import pytest

from potner_docking.approach_controller import DockingGains
from potner_docking.session import DockingPhase, DockingSession, SessionLimits

GAINS = DockingGains()
ALIGNED = (0.14, 5.0, 2.0)  # 거리·좌우·각도 모두 허용치 안
FAR = (1.20, 0.0, 0.0)
CLOSE_BUT_CROOKED = (0.14, 120.0, 0.0)


def session(**kwargs):
    return DockingSession(GAINS, SessionLimits(**kwargs))


def test_마커를_한_번도_못_보면_움직이지_않는다():
    step = session().step(elapsed=0.5, marker_age=float("inf"), observation=None)

    assert step.phase is DockingPhase.SEARCHING
    assert step.linear == 0.0
    assert step.angular == 0.0


def test_마커를_놓친_지_오래되면_정지한다():
    """안 보이는 채로 계속 전진하면 스테이션을 들이받습니다."""
    s = session(marker_lost_timeout=2.0)
    s.step(elapsed=1.0, marker_age=0.0, observation=FAR)

    step = s.step(elapsed=4.0, marker_age=3.0, observation=FAR)

    assert step.phase is DockingPhase.SEARCHING
    assert step.linear == 0.0


def test_마커가_보이고_멀면_전진한다():
    step = session().step(elapsed=1.0, marker_age=0.0, observation=FAR)

    assert step.phase is DockingPhase.APPROACHING
    assert step.linear > 0.0


def test_거리는_됐는데_틀어졌으면_제자리에서_정렬한다():
    step = session().step(elapsed=1.0, marker_age=0.0, observation=CLOSE_BUT_CROOKED)

    assert step.phase is DockingPhase.ALIGNING
    assert step.linear == 0.0
    assert step.angular != 0.0


def test_정렬되면_바로_성공하지_않고_확인_단계로_간다():
    step = session().step(elapsed=1.0, marker_age=0.0, observation=ALIGNED)

    assert step.phase is DockingPhase.CONFIRMING
    assert step.finished is False
    assert step.linear == 0.0


def test_스테이션_접점이_오면_즉시_성공한다():
    step = session().step(
        elapsed=1.0, marker_age=0.0, observation=FAR, station_confirmed=True
    )

    assert step.phase is DockingPhase.DOCKED
    assert step.succeeded is True
    assert step.linear == 0.0


def test_스테이션이_없어도_확인_시간이_지나면_성공한다():
    """스테이션이 아직 안 만들어졌으므로 기본 동작은 비전만으로 성공."""
    s = session(confirm_timeout=5.0, require_station_confirm=False)

    s.step(elapsed=10.0, marker_age=0.0, observation=ALIGNED)
    step = s.step(elapsed=15.1, marker_age=0.0, observation=ALIGNED)

    assert step.phase is DockingPhase.DOCKED
    assert step.succeeded is True


def test_접점을_요구하면_신호_없이는_실패한다():
    """스테이션이 완성되면 이 모드로 바꿔 물리적 접촉을 확인합니다."""
    s = session(confirm_timeout=5.0, require_station_confirm=True)

    s.step(elapsed=10.0, marker_age=0.0, observation=ALIGNED)
    step = s.step(elapsed=15.1, marker_age=0.0, observation=ALIGNED)

    assert step.phase is DockingPhase.FAILED
    assert step.succeeded is False


def test_정렬이_풀리면_확인_타이머가_초기화된다():
    """마커가 흔들려 잠깐 정렬됐다가 풀렸는데도 성공으로 치면 안 됩니다."""
    s = session(confirm_timeout=5.0)

    s.step(elapsed=10.0, marker_age=0.0, observation=ALIGNED)
    s.step(elapsed=12.0, marker_age=0.0, observation=CLOSE_BUT_CROOKED)  # 풀림
    s.step(elapsed=13.0, marker_age=0.0, observation=ALIGNED)  # 다시 정렬

    # 13초에 다시 정렬됐으니 18초 전에는 성공하면 안 됩니다.
    step = s.step(elapsed=17.0, marker_age=0.0, observation=ALIGNED)
    assert step.phase is DockingPhase.CONFIRMING

    step = s.step(elapsed=18.1, marker_age=0.0, observation=ALIGNED)
    assert step.phase is DockingPhase.DOCKED


def test_전체_시간을_넘기면_실패로_끝낸다():
    """무한 루프 방지. legacy 코드에는 이 장치가 없어 영원히 돌 수 있었습니다."""
    step = session(docking_timeout=90.0).step(
        elapsed=91.0, marker_age=0.0, observation=FAR
    )

    assert step.phase is DockingPhase.FAILED
    assert step.finished is True
    assert step.linear == 0.0


def test_실패든_성공이든_끝나면_속도는_0이다():
    for limits, obs, confirmed in (
        ({"docking_timeout": 1.0}, FAR, False),
        ({}, FAR, True),
    ):
        step = session(**limits).step(
            elapsed=50.0, marker_age=0.0, observation=obs, station_confirmed=confirmed
        )
        assert step.finished
        assert step.linear == 0.0
        assert step.angular == 0.0


def test_마지막_관측값을_기억한다():
    """액션 결과에 최종 거리와 각도를 담기 위해 필요합니다."""
    s = session()
    s.step(elapsed=1.0, marker_age=0.0, observation=FAR)

    assert s.last_observation == FAR

    # 마커를 놓쳐도 마지막으로 본 값은 남아 있어야 합니다.
    s.step(elapsed=5.0, marker_age=9.0, observation=None)
    assert s.last_observation == FAR


@pytest.mark.parametrize(
    "phase", [DockingPhase.DOCKED, DockingPhase.FAILED]
)
def test_종료_단계만_finished_로_판정된다(phase):
    from potner_docking.session import DockingStep

    assert DockingStep(phase, 0.0, 0.0, "").finished is True
    assert DockingStep(DockingPhase.APPROACHING, 0.1, 0.0, "").finished is False
