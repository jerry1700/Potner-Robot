"""하드웨어 없이 서버 JSON부터 Jetson 결과 JSON까지 잇는 이동 흐름.

자동 급수 체인이 실제로 도는 순서 그대로다.

    NAVIGATE(WATER_STATION) -> OK -> (서버가 라즈베리에 WATER)
                            -> NAVIGATE(HOME) -> OK -> 체인 완료
"""

import json

from potner_bridge.arrival_contract import (
    WELCOME_START,
    parse_arrival_command,
)
from potner_bridge.command_result import parse_result_envelope, result_message
from potner_bridge.navigate_contract import (
    navigate_result_topic,
    parse_navigate_command,
)
from potner_mission.arrival_session import ArrivalSessionController
from potner_mission.command_session import DecisionKind, MissionResult
from potner_mission.navigate_session import NavigateSessionController

DEVICE_ID = "jetson-01"
STATION_ID = "3ff0d4af-9217-4473-b7a4-f8ff8d420daa"
HOME_ID = "9e2ba2a9-f01e-41c4-bac2-3a21966f20d2"
VISIT_ID = "c8fdcf7e-b91f-464f-b93e-cd3373c107ae"
EVENT_ID = "1c9ad0d2-52a1-4f5b-9d0c-3f0a7d1f0e11"


def server_command(request_id, destination, x=1.25, y=-0.48, yaw=1.5708):
    """서버 NavigateCommandPayload 가 내보내는 그대로."""
    return json.dumps(
        {
            "destination": destination,
            "x": x,
            "y": y,
            "yaw": yaw,
            "requestId": request_id,
        }
    )


def to_mqtt(result: MissionResult) -> dict:
    """mission_manager 결과 -> ROS 내부 봉투 -> MQTT 페이로드."""
    envelope = parse_result_envelope(
        json.dumps(
            {
                "commandName": result.command_name,
                "requestId": result.request_id,
                "status": result.status,
                "error": result.error,
                "code": result.code,
            }
        )
    )
    return json.loads(
        result_message(
            DEVICE_ID,
            envelope["requestId"],
            envelope["status"],
            error=envelope["error"],
            code=envelope["code"],
        )
    )


def test_스테이션_이동_급수_HOME_복귀_전체흐름():
    controller = NavigateSessionController()

    # ① 서버: 급수 스테이션으로 가라
    station = parse_navigate_command(server_command(STATION_ID, "WATER_STATION"))
    assert controller.accept(station, now=0.0, robot_idle=True).kind is (
        DecisionKind.ACCEPTED
    )

    # ② Nav2 도착 -> 급수 스테이션은 마커가 있으므로 정밀 도킹
    assert station.marker_id == 2
    controller.begin_docking()

    # ③ 도킹 완료가 곧 "도착" 이다. OK 는 출발이 아니라 이 시점을 뜻한다.
    body = to_mqtt(controller.reached())
    assert body["status"] == "OK"
    assert body["requestId"] == STATION_ID
    assert navigate_result_topic(DEVICE_ID).endswith("/result/navigate")

    # ④ 서버가 라즈베리에 급수를 시키는 동안 로봇은 그 자리를 지킨다
    assert controller.blocks_arrival is True

    # ⑤ 서버: 이제 대기 장소로 돌아가라 — 마커가 없으니 Nav2 도착이 곧 도착
    home = parse_navigate_command(server_command(HOME_ID, "HOME", 0.1, 0.2, 0.0))
    assert controller.accept(home, now=60.0, robot_idle=True).kind is (
        DecisionKind.ACCEPTED
    )
    assert home.marker_id is None

    body = to_mqtt(controller.reached())
    assert (body["status"], body["requestId"]) == ("OK", HOME_ID)
    assert controller.blocks_arrival is False


def test_QoS1_재전송이_체인을_끊지_않는다():
    """서버가 같은 명령을 두 번 배달해도 급수가 정상으로 끝나야 한다."""
    controller = NavigateSessionController()
    station = parse_navigate_command(server_command(STATION_ID, "WATER_STATION"))
    controller.accept(station, now=0.0, robot_idle=True)

    # 이동 중 재전송 — 회신하지 않는다. BUSY 를 보내면 서버가 이 명령을
    # 실패로 확정해 급수 명령이 영영 나가지 않는다.
    duplicate = controller.accept(station, now=5.0, robot_idle=False)
    assert duplicate.kind is DecisionKind.DUPLICATE_PENDING
    assert duplicate.result is None

    # 도착하면 그제서야 OK 가 한 번 나간다.
    body = to_mqtt(controller.reached())
    assert body["status"] == "OK"


def test_도킹이_실패하면_사유와_함께_ERROR를_보낸다():
    controller = NavigateSessionController()
    station = parse_navigate_command(server_command(STATION_ID, "WATER_STATION"))
    controller.accept(station, now=0.0, robot_idle=True)
    controller.begin_docking()

    body = to_mqtt(
        controller.fail_current("도킹 실패: 마커를 찾지 못함", "DOCKING_FAILED")
    )

    assert body["status"] == "ERROR"
    assert body["code"] == "DOCKING_FAILED"
    assert "마커" in body["error"]


def test_이동_중_다른_명령은_BUSY로_거절한다():
    controller = NavigateSessionController()
    controller.accept(
        parse_navigate_command(server_command(STATION_ID, "WATER_STATION")),
        now=0.0,
        robot_idle=True,
    )

    decision = controller.accept(
        parse_navigate_command(server_command(HOME_ID, "SUNLIGHT")),
        now=1.0,
        robot_idle=False,
    )

    body = to_mqtt(decision.result)
    assert body["status"] == "BUSY"
    assert body["requestId"] == HOME_ID


def test_급수_중_귀가하면_마중을_거절한다():
    """mission_manager 가 두 세션을 묶는 방식 그대로 재현한다.

    스테이션에 대어 놓은 로봇은 상태가 IDLE 이라 상태만으로는 한가해
    보인다. 그대로 마중을 나가면 급수 중에 로봇이 떠나 물이 쏟아진다.
    """
    navigate = NavigateSessionController()
    navigate.accept(
        parse_navigate_command(server_command(STATION_ID, "WATER_STATION")),
        now=0.0,
        robot_idle=True,
    )
    navigate.reached()

    arrival = ArrivalSessionController()
    welcome = parse_arrival_command(
        WELCOME_START,
        json.dumps(
            {
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
                "returnYaw": 0.0,
                "waitSeconds": 120,
                "totalTimeoutSeconds": 300,
            }
        ),
    )

    busy_error, busy_code = navigate.busy_reason()
    decision = arrival.accept_start(
        welcome,
        now=10.0,
        robot_idle=not navigate.blocks_arrival,
        busy_error=busy_error,
        busy_code=busy_code,
    )

    assert decision.kind is DecisionKind.BUSY
    assert decision.result.code == "SERVICING_AT_STATION"
    assert arrival.active is None  # 마중을 시작하지 않았다

    body = to_mqtt(decision.result)
    assert body["status"] == "BUSY"
    assert body["error"] == "급수 스테이션에서 작업 중입니다."


def test_대기_장소에_있을_때는_마중을_나간다():
    navigate = NavigateSessionController()
    navigate.accept(
        parse_navigate_command(server_command(HOME_ID, "HOME", 0.1, 0.2, 0.0)),
        now=0.0,
        robot_idle=True,
    )
    navigate.reached()

    assert navigate.blocks_arrival is False
