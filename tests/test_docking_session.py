"""도킹 세션 상태 전이 검증.

시간을 인자로 받는 구조라 "3초간 마커를 놓쳤을 때" 같은 시나리오를
실제로 기다리지 않고 검증합니다. 실기에서 재현하기 어렵고 실패하면
스테이션을 들이받는 경우들을 여기서 잡습니다.
"""

import math
from dataclasses import replace

import pytest

from potner_docking.approach_controller import DockingGains
from potner_docking.session import DockingPhase, DockingSession, SessionLimits

GAINS = DockingGains()
# 목표 정지 거리(0.15m) 안쪽. 이 값을 목표 거리에 맞춰 두지 않으면
# 도착 판정이 안 서서 이 파일의 시험 대부분이 무의미해진다.
ALIGNED = (0.14, 5.0, 2.0)  # 거리·좌우·각도 모두 허용치 안
FAR = (1.20, 0.0, 0.0)
CLOSE_BUT_CROOKED = (0.14, 120.0, 0.0)


def session(turn_deg=None, **kwargs):
    """turn_deg 를 0 으로 주면 도킹 후 회전 없이 바로 끝냅니다."""
    gains = GAINS if turn_deg is None else replace(
        GAINS, turn_after_dock_deg=turn_deg
    )
    return DockingSession(gains, SessionLimits(**kwargs))


def test_마커를_못_보면_제자리에서_돌며_찾는다():
    """예전에는 0,0 을 내고 가만히 서 있었다.

    로봇이 안 움직이면 장면이 안 바뀌므로 마커가 프러스텀에 다시 들어올
    수 없다. 한 번 놓치면 영영 못 찾는 데드락이었다.

    지금은 먼저 서서 한 번 확인한 뒤에 돈다 — 마커가 이미 앞에 있는데
    돌아서 놓치는 일을 막기 위해서다. 그 확인이 끝나면 돌아야 한다.
    """
    s = session()
    look = s.step(elapsed=0.5, marker_age=float("inf"), observation=None)
    assert look.angular == 0.0  # 먼저 서서 본다

    step = s.step(elapsed=3.0, marker_age=float("inf"), observation=None)

    assert step.phase is DockingPhase.SEARCHING
    assert step.linear == 0.0      # 아직 전진은 안 한다
    assert step.angular != 0.0     # 돌면서 찾는다


def test_마커를_놓친_지_오래되면_정지한다():
    """안 보이는 채로 계속 전진하면 스테이션을 들이받습니다."""
    s = session(marker_lost_timeout=2.0)
    s.step(elapsed=1.0, marker_age=0.0, observation=FAR)

    step = s.step(elapsed=4.0, marker_age=3.0, observation=FAR)

    assert step.phase is DockingPhase.SEARCHING
    assert step.linear == 0.0


def test_방금_놓쳤으면_돌지_않고_멈춰서_기다린다():
    """놓침의 대부분은 자기 움직임이 만든 번짐이다.

    즉시 탐색 회전을 시작하면 그 회전이 방금까지 잘 보이던 마커를 화면
    밖으로 밀어낸다 — 실기에서 "잘 찾다가도 놓치는" 원인이었다. 유예
    안에서는 멈춰서 재검출을 기다려야 한다.
    """
    s = session(marker_lost_timeout=2.0)
    s.step(elapsed=1.0, marker_age=0.0, observation=FAR)

    step = s.step(elapsed=1.5, marker_age=0.5, observation=None)

    assert step.phase is DockingPhase.SEARCHING
    assert step.linear == 0.0
    assert step.angular == 0.0


def test_대기_중에는_누적기를_비우라고_알린다():
    """대기 중 흘러든 각도가 남아 있으면 회전을 시작하자마자
    "한 바퀴 다 돌았다"로 오판한다."""
    s = session(marker_lost_timeout=2.0)
    s.step(elapsed=1.0, marker_age=0.0, observation=FAR)

    step = s.step(elapsed=1.5, marker_age=0.5, observation=None)

    assert step.restart_odometry is True


def test_대기_중_다시_보이면_그대로_접근을_잇는다():
    s = session(marker_lost_timeout=2.0)
    s.step(elapsed=1.0, marker_age=0.0, observation=FAR)
    s.step(elapsed=1.5, marker_age=0.5, observation=None)

    step = s.step(elapsed=2.0, marker_age=0.0, observation=FAR)

    assert step.phase is DockingPhase.APPROACHING
    assert step.linear > 0.0


def test_유예가_지나면_탐색_회전을_시작한다():
    s = session(marker_lost_timeout=2.0)
    s.step(elapsed=1.0, marker_age=0.0, observation=FAR)
    s.step(elapsed=1.5, marker_age=0.5, observation=None)  # 유예 안 — 대기

    s.step(elapsed=4.5, marker_age=3.5, observation=None)  # 먼저 서서 확인
    step = s.step(elapsed=7.0, marker_age=6.0, observation=None)

    assert step.phase is DockingPhase.SEARCHING
    assert step.angular != 0.0


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


def test_회전이_꺼져_있으면_확인_시간_뒤_바로_성공한다():
    """스테이션이 아직 안 만들어졌으므로 판정은 비전만으로 합니다."""
    s = session(turn_deg=0.0, confirm_timeout=5.0, require_station_confirm=False)

    s.step(elapsed=10.0, marker_age=0.0, observation=ALIGNED)
    step = s.step(elapsed=15.1, marker_age=0.0, observation=ALIGNED)

    assert step.phase is DockingPhase.DOCKED
    assert step.succeeded is True


def test_확인_시간이_지나면_회전_단계로_간다():
    """정렬만으로 끝내면 화분이 스테이션 반대쪽을 본 채로 성공이 나가고,
    서버가 곧바로 급수를 시켜 물이 엉뚱한 데로 갑니다."""
    s = session(confirm_timeout=5.0)

    s.step(elapsed=10.0, marker_age=0.0, observation=ALIGNED)
    step = s.step(elapsed=15.1, marker_age=0.0, observation=ALIGNED)

    assert step.phase is DockingPhase.TURNING
    assert step.finished is False
    assert step.linear == 0.0
    assert step.angular != 0.0


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
    assert step.phase is DockingPhase.TURNING


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


def test_마커를_한_번도_못_보면_탐색_제한에서_포기한다():
    """전체 제한만 두면 마커 없는 자리에서 90초를 서 있게 됩니다."""
    s = session(search_timeout=15.0, docking_timeout=90.0)

    step = s.step(elapsed=14.0, marker_age=float("inf"), observation=None)
    assert step.phase is DockingPhase.SEARCHING

    step = s.step(elapsed=15.1, marker_age=float("inf"), observation=None)
    assert step.phase is DockingPhase.FAILED
    assert "찾지 못함" in step.reason


def test_한_번이라도_봤으면_탐색_제한에_걸리지_않는다():
    """접근 중 잠깐 놓친 것과, 애초에 없는 것을 구분해야 합니다."""
    s = session(search_timeout=15.0, docking_timeout=90.0)
    s.step(elapsed=2.0, marker_age=0.0, observation=FAR)

    step = s.step(elapsed=30.0, marker_age=10.0, observation=None)
    assert step.phase is DockingPhase.SEARCHING
    assert step.finished is False


def test_마커를_놓친_주기의_관측은_비어_있다():
    """피드백이 옛 값을 계속 보여주면 정상 동작으로 오해합니다."""
    s = session()
    seen = s.step(elapsed=1.0, marker_age=0.0, observation=FAR)
    lost = s.step(elapsed=5.0, marker_age=9.0, observation=None)

    assert seen.observation == FAR
    assert lost.observation is None


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


# --- 도킹 후 제자리 회전 ---


def _to_turning(confirm_timeout=5.0, **kwargs):
    """회전 단계까지 진행시킨 세션을 돌려줍니다."""
    s = session(confirm_timeout=confirm_timeout, **kwargs)
    s.step(elapsed=10.0, marker_age=0.0, observation=ALIGNED)
    s.step(elapsed=15.1, marker_age=0.0, observation=ALIGNED)
    return s


def _almost_done(s, short_by_deg=4.0):
    """목표를 코앞에 둔 회전량 (rad).

    목표 각도는 오도메트리와 실제 회전의 차이를 흡수하는 보정값이라 언제든
    바뀐다. 숫자를 박아 두면 보정할 때마다 이 파일이 깨지므로, 목표에서
    거꾸로 센다. 정지 마진(2도) 밖이면서 감속 구간 안이어야 한다.
    """
    return math.radians(s.gains.turn_after_dock_deg - short_by_deg)


def test_회전_중에는_마커가_안_보여도_계속_돈다():
    """등을 돌리는 동작이라 마커가 사라지는 것이 정상이다.

    유실 처리로 빠지면 회전을 시작하자마자 SEARCHING 이 되어 영영 못 돈다.
    """
    s = _to_turning()

    step = s.step(
        elapsed=16.0,
        marker_age=float("inf"),
        observation=None,
        turn_progress=math.radians(30.0),
    )

    assert step.phase is DockingPhase.TURNING
    assert step.angular != 0.0


def test_목표_각도를_채우면_성공한다():
    s = _to_turning()

    step = s.step(
        elapsed=22.0,
        marker_age=float("inf"),
        observation=None,
        turn_progress=math.radians(180.0),
    )

    assert step.phase is DockingPhase.DOCKED
    assert step.succeeded is True
    assert step.linear == 0.0
    assert step.angular == 0.0


def test_덜_돈_채로_시한을_넘기면_실패한다():
    """덜 돈 채로 성공을 내면 서버가 급수를 시켜 물이 엉뚱한 데로 간다."""
    s = _to_turning(turn_timeout=20.0)

    step = s.step(
        elapsed=40.0,
        marker_age=float("inf"),
        observation=None,
        turn_progress=math.radians(90.0),
    )

    assert step.phase is DockingPhase.FAILED
    assert step.succeeded is False


def test_회전_중에는_전진하지_않는다():
    """스테이션 코앞이라 조금이라도 전진하면 들이받는다."""
    s = _to_turning()

    for progress in (10.0, 90.0, 170.0):
        step = s.step(
            elapsed=16.0,
            marker_age=float("inf"),
            observation=None,
            turn_progress=math.radians(progress),
        )
        assert step.linear == 0.0


def test_회전_끝이_가까우면_감속한다():
    """정지 명령이 물리 정지가 되기까지 0.2~0.5초가 걸려, 전속으로 문턱을
    지나면 그 지연이 그대로 초과 회전이 된다 (실측 190도)."""
    s = _to_turning()

    fast = s.step(
        elapsed=16.0, marker_age=float("inf"), observation=None,
        turn_progress=math.radians(30.0),
    )
    slow = s.step(
        elapsed=16.5, marker_age=float("inf"), observation=None,
        turn_progress=math.radians(160.0),
    )

    assert 0.0 < slow.angular < fast.angular


def test_감속해도_바닥_속도_아래로는_안_내려간다():
    """너무 느리면 바퀴가 정지마찰을 못 이겨 목표 직전에 정체한다."""
    s = _to_turning()

    step = s.step(
        elapsed=16.0, marker_age=float("inf"), observation=None,
        turn_progress=_almost_done(s),
    )

    assert step.angular == pytest.approx(s.gains.turn_min_speed)


def test_정지_마진_안에_들면_다_돈_것으로_친다():
    """마진 없이 목표각까지 돌리면 정지 지연만큼 항상 지나친다."""
    s = _to_turning()

    step = s.step(
        elapsed=16.0, marker_age=float("inf"), observation=None,
        turn_progress=math.radians(178.5),
    )

    assert step.phase is DockingPhase.DOCKED
    assert step.succeeded is True


def test_목표_코앞_정체는_성공으로_수용한다():
    """TURNING 은 마커 유실 검사를 건너뛰어 출구가 turn_timeout 뿐이다.
    감속 바닥이 정지마찰에 걸려 목표 몇 도 앞에서 멈추면, 기다려 봐야
    "다 돌아놓고 시한 초과 실패"만 남는다."""
    s = _to_turning()
    stuck = _almost_done(s)

    s.step(elapsed=16.0, marker_age=float("inf"), observation=None,
           turn_progress=stuck)
    step = s.step(elapsed=18.6, marker_age=float("inf"), observation=None,
                  turn_progress=stuck)

    assert step.phase is DockingPhase.DOCKED
    assert "수용" in step.reason


def test_많이_남은_정체는_수용하지_않는다():
    """90도나 남았는데 성공을 내면 화분이 옆을 본 채로 급수가 시작된다."""
    s = _to_turning()
    stuck = math.radians(90.0)

    s.step(elapsed=16.0, marker_age=float("inf"), observation=None,
           turn_progress=stuck)
    step = s.step(elapsed=19.0, marker_age=float("inf"), observation=None,
                  turn_progress=stuck)

    assert step.phase is DockingPhase.TURNING  # turn_timeout 이 처리할 몫


# --- 정렬 교착 탈출 ---

CLOSE_SLIGHTLY_OFF = (0.14, 50.0, 5.0)  # 허용치(30px)는 넘고 2배 안


def test_정렬_교착은_시한이_지나면_잔류를_수용하고_회전한다():
    """제자리 회전은 좌우 오차와 기울기를 함께 움직여 둘이 상쇄되는
    평형에 갇힐 수 있다. 후진이 없어 물러났다 다시 붙지도 못하므로,
    90초를 태우는 대신 잔류를 수용하고 넘어간다."""
    s = session(align_timeout=15.0)
    s.step(elapsed=1.0, marker_age=0.0, observation=CLOSE_SLIGHTLY_OFF)

    step = s.step(elapsed=16.5, marker_age=0.0, observation=CLOSE_SLIGHTLY_OFF)

    assert step.phase is DockingPhase.TURNING
    assert "수용" in step.reason


def test_잔류가_크면_시한에서_실패한다():
    """허용치 2배 밖 잔류를 수용하면 단자가 안 맞은 채로 급수가 간다."""
    s = session(align_timeout=15.0)
    s.step(elapsed=1.0, marker_age=0.0, observation=CLOSE_BUT_CROOKED)

    step = s.step(elapsed=16.5, marker_age=0.0, observation=CLOSE_BUT_CROOKED)

    assert step.phase is DockingPhase.FAILED
    assert "정렬 시한" in step.reason


def test_시한_전에는_계속_정렬한다():
    s = session(align_timeout=15.0)
    s.step(elapsed=1.0, marker_age=0.0, observation=CLOSE_SLIGHTLY_OFF)

    step = s.step(elapsed=10.0, marker_age=0.0, observation=CLOSE_SLIGHTLY_OFF)

    assert step.phase is DockingPhase.ALIGNING


def test_교착_시한을_끄면_전체_제한까지_정렬한다():
    s = session(align_timeout=0.0)
    s.step(elapsed=1.0, marker_age=0.0, observation=CLOSE_SLIGHTLY_OFF)

    step = s.step(elapsed=40.0, marker_age=0.0, observation=CLOSE_SLIGHTLY_OFF)

    assert step.phase is DockingPhase.ALIGNING


def test_유실_후_멀리서_다시_찾으면_교착_시한이_발동하지_않는다():
    """낡은 시계로 잔류 수용이 발동하면 스테이션에서 0.5m 떨어진 채
    "성공"이 나가고, 서버가 그 자리에서 급수를 시작한다."""
    s = session(align_timeout=15.0)
    s.step(elapsed=1.0, marker_age=0.0, observation=CLOSE_SLIGHTLY_OFF)  # 가까이
    s.step(elapsed=5.0, marker_age=4.0, observation=None)  # 유실 -> 탐색

    step = s.step(elapsed=18.0, marker_age=0.0, observation=(0.50, 40.0, 5.0))

    assert step.phase is DockingPhase.APPROACHING  # 수용도 실패도 아니고 재접근


def test_재접근하면_교착_시한을_처음부터_다시_잰다():
    s = session(align_timeout=15.0)
    s.step(elapsed=1.0, marker_age=0.0, observation=CLOSE_SLIGHTLY_OFF)
    s.step(elapsed=18.0, marker_age=0.0, observation=(0.50, 40.0, 5.0))  # 되감김

    # 20초에 다시 목표 거리 도착 — 시한은 35초부터라 아직 정렬 중이어야 한다
    step = s.step(elapsed=20.0, marker_age=0.0, observation=CLOSE_SLIGHTLY_OFF)
    assert step.phase is DockingPhase.ALIGNING

    step = s.step(elapsed=36.0, marker_age=0.0, observation=CLOSE_SLIGHTLY_OFF)
    assert step.phase is DockingPhase.TURNING  # 새 시계 기준 15초 초과 -> 수용


def test_반대_방향_회전도_감속_구간에서_방향이_유지된다():
    """크기만 바닥을 깔면 음수 속도(우회전 설정)일 때 감속 구간에서
    부호가 뒤집혀 경계에서 영영 왔다갔다 한다."""
    s = DockingSession(replace(GAINS, turn_speed=-0.5), SessionLimits())
    s.step(elapsed=10.0, marker_age=0.0, observation=ALIGNED)
    s.step(elapsed=15.1, marker_age=0.0, observation=ALIGNED)  # TURNING 진입

    step = s.step(
        elapsed=16.0, marker_age=float("inf"), observation=None,
        turn_progress=math.radians(160.0),  # 감속 구간 한복판
    )

    assert step.angular < 0.0
