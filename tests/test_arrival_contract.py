"""서버와 Jetson 사이 귀가 MQTT 계약 검증."""

import json
import math

import pytest

from potner_bridge.arrival_contract import (
    WELCOME_CANCEL,
    WELCOME_START,
    ArrivalCommandError,
    WelcomeCancelCommand,
    WelcomeStartCommand,
    arrival_command_json,
    arrival_result_message,
    arrival_result_topic,
    parse_arrival_command,
    parse_internal_arrival_command,
    parse_result_envelope,
    result_envelope,
)

EVENT_ID = "3ff0d4af-9217-4473-b7a4-f8ff8d420daa"
VISIT_ID = "c8fdcf7e-b91f-464f-b93e-cd3373c107ae"


def start_payload(**overrides):
    body = {
        "eventId": EVENT_ID,
        "visitId": VISIT_ID,
        "requestId": EVENT_ID,
        "destination": "GREETING",
        "x": 1.25,
        "y": -0.48,
        "yaw": 1.57,
        "returnDestination": "HOME",
        "returnX": 0.1,
        "returnY": 0.2,
        "returnYaw": -1.0,
        "waitSeconds": 120,
        "totalTimeoutSeconds": 300,
        "publishedAt": "2026-07-31T08:00:00Z",
    }
    body.update(overrides)
    return json.dumps(body)


def cancel_payload(**overrides):
    body = {
        "eventId": EVENT_ID,
        "visitId": VISIT_ID,
        "requestId": EVENT_ID,
        "returnDestination": "HOME",
        "returnX": 0.1,
        "returnY": 0.2,
        "returnYaw": -1.0,
        "publishedAt": "2026-07-31T08:00:00Z",
    }
    body.update(overrides)
    return json.dumps(body)


def test_welcome_start_를_동적_GREETING_HOME_좌표로_해석한다():
    command = parse_arrival_command(WELCOME_START, start_payload().encode())

    assert isinstance(command, WelcomeStartCommand)
    assert (command.greeting.x, command.greeting.y, command.greeting.yaw) == (
        1.25,
        -0.48,
        1.57,
    )
    assert (command.home.x, command.home.y, command.home.yaw) == (0.1, 0.2, -1.0)
    assert command.wait_seconds == 120
    assert command.total_timeout_seconds == 300


def test_welcome_cancel_은_HOME_복귀만_해석한다():
    command = parse_arrival_command(WELCOME_CANCEL, cancel_payload())

    assert isinstance(command, WelcomeCancelCommand)
    assert command.visit_id == VISIT_ID
    assert command.home.x == 0.1


def test_지도_원점도_정상_좌표다():
    command = parse_arrival_command(
        WELCOME_START,
        start_payload(
            x=0.0,
            y=0.0,
            yaw=0.0,
            returnX=0.0,
            returnY=0.0,
            returnYaw=0.0,
        ),
    )

    assert command.greeting.x == 0.0
    assert command.home.x == 0.0


@pytest.mark.parametrize(
    "payload,code",
    [
        ("not-json", "INVALID_JSON"),
        ("[]", "INVALID_PAYLOAD"),
        (start_payload(destination="HOME"), "INVALID_DESTINATION"),
        (start_payload(returnDestination="GREETING"), "INVALID_DESTINATION"),
        (start_payload(requestId="not-uuid"), "INVALID_IDENTIFIER"),
        (start_payload(visitId="not-uuid"), "INVALID_IDENTIFIER"),
        (
            start_payload(
                requestId="c8fdcf7e-b91f-464f-b93e-cd3373c107ae"
            ),
            "MISMATCHED_REQUEST_ID",
        ),
        (start_payload(waitSeconds=True), "INVALID_TIMEOUT"),
        (start_payload(waitSeconds=0), "INVALID_TIMEOUT"),
        (start_payload(totalTimeoutSeconds=120), "INVALID_TIMEOUT"),
        (start_payload(yaw=math.pi + 0.01), "INVALID_POSE"),
        (start_payload(x="1.0"), "INVALID_POSE"),
    ],
)
def test_위험하거나_대조할_수_없는_명령은_거부한다(payload, code):
    with pytest.raises(ArrivalCommandError) as error:
        parse_arrival_command(WELCOME_START, payload)

    assert error.value.code == code


def test_검증한_명령은_ROS_내부_전달_후에도_같다():
    original = parse_arrival_command(WELCOME_START, start_payload())
    restored = parse_internal_arrival_command(arrival_command_json(original))

    assert restored == original


def test_결과_토픽과_서버_결과_JSON이_맞는다():
    assert arrival_result_topic("jetson-01", WELCOME_START) == (
        "potner/device/jetson-01/result/welcome_start"
    )
    result = json.loads(
        arrival_result_message(
            "jetson-01",
            EVENT_ID,
            "ERROR",
            error="Nav2 목표 도달 실패",
            code="NAVIGATION_FAILED",
            message_id="0b180b7a-41c6-4ad1-8519-5ebf89ddf6df",
        )
    )
    assert result == {
        "messageId": "0b180b7a-41c6-4ad1-8519-5ebf89ddf6df",
        "deviceId": "jetson-01",
        "requestId": EVENT_ID,
        "status": "ERROR",
        "error": "Nav2 목표 도달 실패",
        "code": "NAVIGATION_FAILED",
    }


def test_ROS_결과_봉투를_MQTT_bridge가_복원한다():
    payload = result_envelope(
        WELCOME_CANCEL,
        EVENT_ID,
        "BUSY",
        error="다른 임무 수행 중",
        code="ROBOT_BUSY",
    )

    assert parse_result_envelope(payload) == {
        "commandName": WELCOME_CANCEL,
        "requestId": EVENT_ID,
        "status": "BUSY",
        "error": "다른 임무 수행 중",
        "code": "ROBOT_BUSY",
    }
