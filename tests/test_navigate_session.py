"""이동 명령 상태 머신 — 하드웨어 없이 중복·동시·실패·파킹을 검증한다."""

import pytest

from potner_bridge.command_result import MapPose
from potner_bridge.navigate_contract import NavigateCommand
from potner_mission.command_session import DecisionKind
from potner_mission.navigate_session import (
    NavigateSessionController,
    NavigateStage,
)

STATION_ID = "3ff0d4af-9217-4473-b7a4-f8ff8d420daa"
HOME_ID = "9e2ba2a9-f01e-41c4-bac2-3a21966f20d2"


def command(request_id=STATION_ID, destination="WATER_STATION"):
    return NavigateCommand(
        request_id=request_id,
        destination=destination,
        pose=MapPose(1.25, -0.48, 1.5708),
    )


def accepted_controller(**kwargs):
    controller = NavigateSessionController()
    decision = controller.accept(command(**kwargs), now=100.0, robot_idle=True)
    assert decision.kind is DecisionKind.ACCEPTED
    return controller


# --- 정상 흐름 ---


def test_도착하면_OK를_한_번_회신한다():
    controller = accepted_controller()
    controller.begin_docking()

    result = controller.reached()

    assert (result.status, result.request_id) == ("OK", STATION_ID)
    assert result.error is None and result.code is None
    assert controller.active is None


def test_도킹_실패는_ERROR와_사유를_함께_회신한다():
    controller = accepted_controller()
    controller.begin_docking()

    result = controller.fail_current("도킹 실패: 마커를 찾지 못함", "DOCKING_FAILED")

    assert result.status == "ERROR"
    assert result.code == "DOCKING_FAILED"
    assert "마커" in result.error
    assert controller.active is None


def test_주행_실패도_ERROR다():
    controller = accepted_controller()

    result = controller.fail_current("Nav2 이동 실패(status=6)", "NAVIGATION_FAILED")

    assert (result.status, result.code) == ("ERROR", "NAVIGATION_FAILED")


def test_도킹_단계로_넘어간_것을_기억한다():
    controller = accepted_controller()
    assert controller.active.stage is NavigateStage.NAVIGATING

    controller.begin_docking()

    assert controller.active.stage is NavigateStage.DOCKING


# --- 중복 수신 (QoS 1 재전송) ---


def test_수행_중인_명령이_다시_오면_아무것도_회신하지_않는다():
    """★ 여기서 BUSY 를 보내면 자동 케어 체인이 통째로 끊긴다.

    서버는 BUSY 도 종단 상태로 본다(DeviceCommandStatus.isTerminal). 재전송에
    BUSY 를 회신하면 서버가 수행 중인 명령을 실패로 확정하고, 체인은
    status != OK 에서 멈추며, 진짜 도착해서 보낸 OK 는 ALREADY_COMPLETED 로
    버려진다. 로봇은 스테이션 앞에 서 있는데 물은 나오지 않는다.
    """
    controller = accepted_controller()

    decision = controller.accept(command(), now=101.0, robot_idle=False)

    assert decision.kind is DecisionKind.DUPLICATE_PENDING
    assert decision.result is None  # 회신 없음


def test_중복_수신이_같은_이동을_다시_시작하지_않는다():
    controller = accepted_controller()
    controller.begin_docking()

    controller.accept(command(), now=101.0, robot_idle=False)

    # 세션이 그대로다. 단계가 NAVIGATING 으로 되감기지 않는다.
    assert controller.active.stage is NavigateStage.DOCKING
    assert controller.active.started_at == 100.0


def test_이미_끝낸_명령이_다시_오면_그때_결과를_다시_보낸다():
    """결과가 유실됐을 수 있다. 서버는 이미 반영했으면 조용히 흘린다."""
    controller = accepted_controller()
    original = controller.reached()

    decision = controller.accept(command(), now=200.0, robot_idle=True)

    assert decision.kind is DecisionKind.REPLAY
    assert decision.result == original


def test_재전송_회신이_새_이동을_만들지_않는다():
    controller = accepted_controller()
    controller.reached()

    controller.accept(command(), now=200.0, robot_idle=True)

    assert controller.active is None


# --- 동시 명령 ---


def test_이동_중에_다른_명령이_오면_BUSY다():
    controller = accepted_controller()

    decision = controller.accept(
        command(request_id=HOME_ID, destination="HOME"), now=110.0, robot_idle=False
    )

    assert decision.kind is DecisionKind.BUSY
    assert decision.result.status == "BUSY"
    assert decision.result.request_id == HOME_ID
    assert decision.result.code == "ROBOT_BUSY"
    # 수행 중이던 명령은 그대로다.
    assert controller.active.request_id == STATION_ID


def test_다른_임무가_로봇을_붙들고_있으면_BUSY다():
    controller = NavigateSessionController()

    decision = controller.accept(command(), now=100.0, robot_idle=False)

    assert decision.kind is DecisionKind.BUSY
    assert controller.active is None


def test_도착_뒤에는_다음_이동_명령을_받는다():
    """급수를 마친 뒤 HOME 복귀 명령이 BUSY 로 막히면 체인이 죽는다."""
    controller = accepted_controller()
    controller.reached()

    decision = controller.accept(
        command(request_id=HOME_ID, destination="HOME"), now=200.0, robot_idle=True
    )

    assert decision.kind is DecisionKind.ACCEPTED


# --- 스테이션 파킹 ---


def test_급수_스테이션에_대어_놓은_동안은_마중을_막는다():
    controller = accepted_controller()
    controller.reached()

    assert controller.parked_at == "WATER_STATION"
    assert controller.blocks_arrival is True
    assert controller.busy_reason() == (
        "급수 스테이션에서 작업 중입니다.",
        "SERVICING_AT_STATION",
    )


@pytest.mark.parametrize("destination", ["HOME", "SUNLIGHT", "GREETING"])
def test_다른_자리에_서_있는_것은_한가한_것이다(destination):
    controller = accepted_controller(destination=destination)
    controller.reached()

    assert controller.parked_at == destination
    assert controller.blocks_arrival is False
    assert controller.busy_reason() == (None, None)


def test_이동_중에도_마중을_막는다():
    controller = accepted_controller(destination="HOME")

    assert controller.blocks_arrival is True
    assert controller.busy_reason() == ("서버 이동 명령을 수행 중입니다.", "NAVIGATING")


def test_다음_이동_명령을_받으면_파킹이_풀린다():
    controller = accepted_controller()
    controller.reached()

    controller.accept(
        command(request_id=HOME_ID, destination="HOME"), now=200.0, robot_idle=True
    )

    assert controller.parked_at is None


def test_실패하면_스테이션에_있다고_가정하지_않는다():
    """어디에 멈췄는지 알 수 없다. 스테이션이라고 보면 마중을 영영 막는다."""
    controller = accepted_controller()
    controller.fail_current("Nav2 이동 실패", "NAVIGATION_FAILED")

    assert controller.parked_at is None
    assert controller.blocks_arrival is False


def test_스테이션_파킹은_SERVICING_상태로_보고할_근거다():
    """서버는 SERVICING 을 보고 표정을 매우행복으로 바꾼다.

    로봇은 급수가 끝났는지 알 방법이 없다. 다음 이동 명령이 오는 것이
    유일한 신호라, 그때까지 parked_at 이 남아 상태를 유지시킨다.
    """
    controller = accepted_controller()
    controller.reached()
    assert controller.parked_at == "WATER_STATION"

    # HOME 복귀 명령을 받으면 그 근거가 사라진다.
    controller.accept(
        command(request_id=HOME_ID, destination="HOME"), now=200.0, robot_idle=True
    )
    assert controller.parked_at is None


def test_자리를_떴다고_알리면_파킹이_풀린다():
    controller = accepted_controller()
    controller.reached()

    controller.left_station()

    assert controller.blocks_arrival is False


# --- 보조 ---


def test_결과_캐시는_무한히_자라지_않는다():
    controller = NavigateSessionController(result_cache_size=2)
    for index in range(4):
        request_id = f"cmd-{index}"
        controller.accept(command(request_id=request_id), now=100.0, robot_idle=True)
        controller.reached()

    assert controller.accept(command(request_id="cmd-0"), 200.0, True).kind is (
        DecisionKind.ACCEPTED  # 밀려났다
    )
    controller.fail_current("정리", "CLEANUP")
    assert controller.accept(command(request_id="cmd-3"), 200.0, True).kind is (
        DecisionKind.REPLAY  # 남아 있다
    )


def test_진행_중인_명령_없이_도착할_수_없다():
    with pytest.raises(RuntimeError):
        NavigateSessionController().reached()


def test_진행_중인_명령이_없으면_실패_회신도_없다():
    assert NavigateSessionController().fail_current("무엇", "NONE") is None
