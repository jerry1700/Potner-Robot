"""하드웨어 없이 서버 JSON부터 Jetson 결과 JSON까지 잇는 귀가 흐름."""

import json

from potner_bridge.arrival_contract import (
    WELCOME_CANCEL,
    WELCOME_START,
    arrival_result_message,
    parse_arrival_command,
)
from potner_mission.arrival_session import (
    ArrivalSessionController,
    DecisionKind,
)

START_ID = "3ff0d4af-9217-4473-b7a4-f8ff8d420daa"
CANCEL_ID = "9e2ba2a9-f01e-41c4-bac2-3a21966f20d2"
VISIT_ID = "c8fdcf7e-b91f-464f-b93e-cd3373c107ae"


def test_start_GREETING_OK_대기_HOME_전체흐름():
    command = parse_arrival_command(
        WELCOME_START,
        json.dumps(
            {
                "eventId": START_ID,
                "visitId": VISIT_ID,
                "requestId": START_ID,
                "destination": "GREETING",
                "x": 1.25,
                "y": -0.48,
                "yaw": 1.57,
                "returnDestination": "HOME",
                "returnX": 0.1,
                "returnY": 0.2,
                "returnYaw": 0.0,
                "waitSeconds": 120,
                "totalTimeoutSeconds": 300,
            }
        ),
    )
    controller = ArrivalSessionController()

    decision = controller.accept_start(command, now=100.0, robot_idle=True)
    assert decision.kind is DecisionKind.ACCEPTED

    result = controller.greeting_reached(now=130.0)
    mqtt_result = json.loads(
        arrival_result_message(
            "jetson-01",
            result.request_id,
            result.status,
            message_id="0b180b7a-41c6-4ad1-8519-5ebf89ddf6df",
        )
    )
    assert mqtt_result["requestId"] == START_ID
    assert mqtt_result["status"] == "OK"

    assert controller.waiting_expired(250.0)
    controller.begin_return_home()
    assert controller.home_reached() is None
    assert controller.active is None


def test_cancel은_같은_visit을_HOME_OK까지_잇는다():
    start = parse_arrival_command(
        WELCOME_START,
        json.dumps(
            {
                "eventId": START_ID,
                "visitId": VISIT_ID,
                "requestId": START_ID,
                "destination": "GREETING",
                "x": 1.0,
                "y": 2.0,
                "yaw": 0.0,
                "returnDestination": "HOME",
                "returnX": 0.0,
                "returnY": 0.0,
                "returnYaw": 0.0,
                "waitSeconds": 120,
                "totalTimeoutSeconds": 300,
            }
        ),
    )
    cancel = parse_arrival_command(
        WELCOME_CANCEL,
        json.dumps(
            {
                "eventId": CANCEL_ID,
                "visitId": VISIT_ID,
                "requestId": CANCEL_ID,
                "returnDestination": "HOME",
                "returnX": 0.0,
                "returnY": 0.0,
                "returnYaw": 0.0,
            }
        ),
    )
    controller = ArrivalSessionController()
    controller.accept_start(start, now=100.0, robot_idle=True)

    decision = controller.accept_cancel(cancel, now=110.0, robot_idle=False)
    assert decision.kind is DecisionKind.ACCEPTED

    result = controller.home_reached()
    mqtt_result = json.loads(
        arrival_result_message(
            "jetson-01",
            result.request_id,
            result.status,
            message_id="7f123313-1a30-4faf-b3f3-4655433465db",
        )
    )
    assert mqtt_result["requestId"] == CANCEL_ID
    assert mqtt_result["status"] == "OK"
