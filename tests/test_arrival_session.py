"""로봇 없이 검증하는 귀가 마중 상태 머신."""

from potner_bridge.arrival_contract import (
    MapPose,
    WelcomeCancelCommand,
    WelcomeStartCommand,
)
from potner_mission.arrival_session import (
    ArrivalSessionController,
    ArrivalStage,
    DecisionKind,
)

START_ID = "3ff0d4af-9217-4473-b7a4-f8ff8d420daa"
CANCEL_ID = "9e2ba2a9-f01e-41c4-bac2-3a21966f20d2"
OTHER_ID = "c8fdcf7e-b91f-464f-b93e-cd3373c107ae"
GREETING = MapPose(1.0, 2.0, 0.3)
HOME = MapPose(0.0, 0.0, 0.0)


def start(request_id=START_ID, visit_id="visit-1"):
    return WelcomeStartCommand(
        request_id,
        visit_id,
        request_id,
        GREETING,
        HOME,
        120,
        300,
    )


def cancel(request_id=CANCEL_ID, visit_id="visit-1"):
    return WelcomeCancelCommand(request_id, visit_id, request_id, HOME)


def test_GREETING_도착할_때_start_OK_를_확정한다():
    controller = ArrivalSessionController()

    assert controller.accept_start(start(), 100.0, robot_idle=True).kind is (
        DecisionKind.ACCEPTED
    )
    assert controller.active.stage is ArrivalStage.NAVIGATING_GREETING

    result = controller.greeting_reached(130.0)
    assert result.status == "OK"
    assert result.request_id == START_ID
    assert controller.active.stage is ArrivalStage.WAITING_AT_GREETING


def test_맞이_대기시간이_지나면_HOME_복귀_조건이_된다():
    controller = ArrivalSessionController()
    controller.accept_start(start(), 100.0, robot_idle=True)
    controller.greeting_reached(130.0)

    assert controller.waiting_expired(249.9) is False
    assert controller.waiting_expired(250.0) is True


def test_전체_제한시간은_이동과_대기를_포함한다():
    controller = ArrivalSessionController()
    controller.accept_start(start(), 100.0, robot_idle=True)

    assert controller.total_timeout_expired(399.9) is False
    assert controller.total_timeout_expired(400.0) is True


def test_같은_visit_취소는_즉시_HOME_복귀로_전환한다():
    controller = ArrivalSessionController()
    controller.accept_start(start(), 100.0, robot_idle=True)

    decision = controller.accept_cancel(cancel(), 110.0, robot_idle=False)

    assert decision.kind is DecisionKind.ACCEPTED
    assert controller.active.stage is ArrivalStage.NAVIGATING_HOME
    result = controller.home_reached()
    assert result.status == "OK"
    assert result.request_id == CANCEL_ID
    assert controller.active is None


def test_로봇_재시작_후_cancel만_와도_HOME으로_복귀한다():
    controller = ArrivalSessionController()

    decision = controller.accept_cancel(cancel(), 100.0, robot_idle=True)

    assert decision.kind is DecisionKind.ACCEPTED
    assert controller.active.home == HOME


def test_다른_visit의_취소는_BUSY다():
    controller = ArrivalSessionController()
    controller.accept_start(start(), 100.0, robot_idle=True)

    decision = controller.accept_cancel(
        cancel(OTHER_ID, visit_id="visit-2"), 110.0, robot_idle=False
    )

    assert decision.kind is DecisionKind.BUSY
    assert decision.result.status == "BUSY"
    assert controller.active.visit_id == "visit-1"


def test_중복_진행명령은_새_이동을_만들지_않는다():
    controller = ArrivalSessionController()
    controller.accept_start(start(), 100.0, robot_idle=True)

    decision = controller.accept_start(start(), 101.0, robot_idle=False)

    assert decision.kind is DecisionKind.DUPLICATE_PENDING


def test_다른_ID의_두번째_취소는_첫_HOME_복귀를_덮어쓰지_않는다():
    controller = ArrivalSessionController()
    controller.accept_start(start(), 100.0, robot_idle=True)
    controller.accept_cancel(cancel(), 110.0, robot_idle=False)

    decision = controller.accept_cancel(
        cancel(OTHER_ID), 111.0, robot_idle=False
    )

    assert decision.kind is DecisionKind.BUSY
    assert controller.active.cancel_request_id == CANCEL_ID


def test_완료된_중복명령은_같은_결과를_재전송한다():
    controller = ArrivalSessionController()
    controller.accept_start(start(), 100.0, robot_idle=True)
    first = controller.greeting_reached(110.0)

    decision = controller.accept_start(start(), 120.0, robot_idle=False)

    assert decision.kind is DecisionKind.REPLAY
    assert decision.result == first


def test_GREETING_이동실패는_start_ERROR다():
    controller = ArrivalSessionController()
    controller.accept_start(start(), 100.0, robot_idle=True)

    result = controller.fail_current("Nav2 이동 실패", "NAVIGATION_FAILED")

    assert result.status == "ERROR"
    assert result.request_id == START_ID
    assert controller.active.stage is ArrivalStage.NAVIGATING_HOME
