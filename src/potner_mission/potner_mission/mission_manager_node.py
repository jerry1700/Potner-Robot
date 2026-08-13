"""mission_manager — 서버가 내린 명령을 실제 움직임으로 바꾸는 곳.

**로봇은 스스로 갈 곳을 정하지 않습니다.** 센서를 읽고 판단하는 쪽은
서버이고, 여기는 받은 명령을 수행하고 결과를 되돌립니다. 예전에는 젯슨이
센서 임계값으로 임무를 정했지만(legacy/main.py 의 우선순위 스케줄러),
서버와 로봇이 동시에 로봇을 움직여 왕복하는 문제가 있어 걷어냈습니다.

명령 두 갈래:

    navigate       서버 좌표로 이동 ─(급수 스테이션이면 도킹)─> OK 회신
    welcome_start  GREETING 으로 마중 ─(사람/시간 만료)─> HOME 복귀

    IDLE ─> NAVIGATING ─> DOCKING ─> SERVICING(스테이션 파킹) ─> IDLE
       └─> NAVIGATING ─> GREETING ─> IDLE

navigate 는 자동 급수·촬영·말리기·햇빛 이동의 시작점입니다. 급수
스테이션에 도착하면 서버가 라즈베리에 급수·송풍·촬영을 시키는데, 젯슨은
그게 끝났는지 알 방법이 없습니다. 그래서 **다음 이동 명령이 올 때까지 그
자리를 SERVICING 으로 지킵니다** — 그 동안 마중 명령은 거절합니다. 물을
받다 말고 떠나면 바닥에 쏟기 때문입니다.

인사는 아무 때나 하지 않습니다. 서버의 welcome_start가 인사를 무장시키고,
GREETING 도착 뒤 waitSeconds 안에 카메라가 사람을 보면 그때 인사합니다.
welcome_cancel은 진행 중 이동과 대기를 끊고 HOME으로 복귀시킵니다.

**블로킹하지 않습니다.** Nav2와 도킹을 액션으로 보내고 결과를 콜백으로
받아, 기다리는 동안에도 다른 이벤트를 처리합니다.

Nav2 와 도킹의 역할을 나눈 이유가 있습니다. Nav2 는 지도 좌표까지 잘
데려다주지만 도착 오차가 수십 cm 입니다. 급수 노즐과 카메라를 맞추려면
cm 단위가 필요해서 마지막 구간만 마커를 보는 전용 컨트롤러가 맡습니다.
"""

import math
from enum import Enum, auto

import rclpy
from geometry_msgs.msg import PoseStamped
from nav2_msgs.action import NavigateToPose
from rclpy.action import ActionClient
from rclpy.node import Node
from std_msgs.msg import Bool, String

from potner_bridge.arrival_contract import (
    ArrivalCommandError,
    WelcomeCancelCommand,
    WelcomeStartCommand,
    parse_internal_arrival_command,
    result_envelope,
)
from potner_bridge.command_result import CommandError
from potner_bridge.navigate_contract import (
    DOCKING_MARKERS,
    WATER_STATION,
    parse_internal_navigate_command,
)
from potner_msgs.action import DockToStation
from potner_mission.arrival_session import (
    ArrivalSessionController,
    ArrivalStage,
    DecisionKind,
)
from potner_mission.greeting import GreetingPolicy
from potner_mission.navigate_session import NavigateSessionController

# Nav2 목표를 누가 왜 보냈는지. 도착했을 때 무엇을 할지가 여기서 갈립니다.
NAV_ARRIVAL_GREETING = "arrival_greeting"
NAV_ARRIVAL_HOME = "arrival_home"
NAV_SERVER = "server_navigate"    # 서버 command/navigate


class MissionState(Enum):
    IDLE = auto()
    NAVIGATING = auto()
    DOCKING = auto()
    SERVICING = auto()
    GREETING = auto()


class MissionManager(Node):
    def __init__(self):
        super().__init__("mission_manager")

        self.declare_parameter("state_publish_period", 2.0)
        self.declare_parameter("greeting_cooldown", 300.0)
        self.declare_parameter("docking_timeout", 90.0)
        self.declare_parameter("arrival_home_timeout", 120.0)
        self.declare_parameter("navigate_timeout", 300.0)
        self.declare_parameter("skip_navigation", False)

        self.state = MissionState.IDLE
        self.greeting = GreetingPolicy(
            cooldown=self.get_parameter("greeting_cooldown").value,
        )
        self._arrival = ArrivalSessionController()
        self._navigate = NavigateSessionController()
        self._arrival_wait_timer = None
        self._arrival_timeout_timer = None
        self._navigate_timeout_timer = None
        # Nav2 를 서버 이동과 마중 이동이 같이 쓰므로, 도착했을 때 도킹으로
        # 넘길지 제자리 대기로 넘길지를 이걸로 구분합니다.
        self._nav_purpose = None
        self._nav_goal_handle = None
        self._nav_generation = 0

        # 식물 센서는 구독하지 않습니다. 판단하는 쪽이 서버라서 로봇이
        # 값을 볼 이유가 없습니다. 측정값은 plant_sensors -> mqtt_bridge 로
        # 곧장 갑니다.

        # 사람 감지 (person_detector 노드가 발행)
        self.create_subscription(Bool, "perception/person_present", self._on_person, 10)

        # mqtt_bridge가 검증한 실제 서버 welcome_start/cancel 명령.
        self.create_subscription(
            String, "mission/arrival_command", self._on_arrival_command, 10
        )
        # 서버 command/navigate. 자동 급수·촬영·말리기·햇빛 이동의 시작점.
        self.create_subscription(
            String, "mission/navigate_command", self._on_navigate_command, 10
        )

        self._speech_pub = self.create_publisher(String, "tts/say", 10)
        # 인사하는 순간의 표정. 평소 표정은 서버가 정하지만(30초 주기 반복),
        # 인사는 로봇이 시작하는 이벤트라 즉시 반응이 필요합니다. 서버 주기가
        # 돌아오면 서버 값으로 자연히 덮입니다.
        self._expression_pub = self.create_publisher(String, "display/expression", 10)
        self._state_pub = self.create_publisher(String, "mission/state", 10)
        self._arrival_result_pub = self.create_publisher(
            String, "mission/arrival_result", 10
        )
        self._navigate_result_pub = self.create_publisher(
            String, "mission/navigate_result", 10
        )
        self._nav_client = ActionClient(self, NavigateToPose, "navigate_to_pose")
        self._dock_client = ActionClient(self, DockToStation, "dock_to_station")

        self.create_timer(
            self.get_parameter("state_publish_period").value, self._publish_state
        )
        self.get_logger().info("mission_manager 시작 (상태: IDLE)")

    # --- 입력 ---

    def _now_s(self):
        return self.get_clock().now().nanoseconds * 1e-9

    def _on_arrival_command(self, msg: String):
        """검증된 welcome_start/cancel을 실제 Nav2 임무로 바꿉니다."""
        try:
            command = parse_internal_arrival_command(msg.data)
        except ArrivalCommandError as exc:
            self.get_logger().error(f"귀가 내부 명령 거부: {exc}")
            return

        # 서버 이동 명령이 로봇을 붙들고 있으면 마중을 나가지 않습니다.
        # 급수 스테이션에 대어 놓은 동안이 특히 그렇습니다 — 서버가
        # 라즈베리에 급수·촬영을 시키는 중인데 로봇이 떠나면 물이 바닥에
        # 쏟아집니다. 그 자리에서는 상태가 IDLE 이라 상태만으로는 알 수
        # 없어서 이동 세션에 따로 물어봅니다.
        busy_error, busy_code = self._navigate.busy_reason()
        robot_idle = (
            self.state is MissionState.IDLE and not self._navigate.blocks_arrival
        )
        if isinstance(command, WelcomeStartCommand):
            decision = self._arrival.accept_start(
                command,
                self._now_s(),
                robot_idle,
                busy_error=busy_error,
                busy_code=busy_code,
            )
            if self._finish_decision(decision):
                return
            # 인사 무장은 여기서 하지 않습니다 — GREETING 에 **도착한 뒤**
            # 겁니다(_handle_nav_success). 여기서 걸면 시간창은 지금부터
            # wait_seconds 인데 문 앞 대기 타이머는 도착부터 다시 세기
            # 때문에, 이동 시간만큼 시간창이 앞에서 잘려 나갑니다. 이동이
            # wait_seconds 보다 길면 도착하는 순간 이미 만료돼 있어서
            # **한 번도 인사할 수 없습니다.** skip_navigation:true 로만
            # 시험해서(이동 시간 0) 지금까지 드러나지 않았습니다.
            self._start_arrival_timeout(command.total_timeout_seconds)
            self.get_logger().info(
                f"귀가 마중 시작: visitId={command.visit_id}, "
                f"GREETING={command.greeting}, HOME={command.home}"
            )
            self._start_arrival_navigation(
                command.greeting, purpose=NAV_ARRIVAL_GREETING
            )
            return

        if isinstance(command, WelcomeCancelCommand):
            decision = self._arrival.accept_cancel(
                command,
                self._now_s(),
                robot_idle,
                busy_error=busy_error,
                busy_code=busy_code,
            )
            if self._finish_decision(decision):
                return
            self.get_logger().info(
                f"귀가 마중 취소: visitId={command.visit_id} -> HOME"
            )
            self.greeting.disarm()
            self._cancel_arrival_timers()
            home_timeout = int(
                self.get_parameter("arrival_home_timeout").value
            )
            self._arrival.reset_total_timeout(self._now_s(), home_timeout)
            self._start_arrival_timeout(home_timeout)
            self._cancel_active_navigation()
            self._start_arrival_navigation(
                command.home, purpose=NAV_ARRIVAL_HOME
            )

    def _finish_decision(self, decision):
        if decision.kind is DecisionKind.ACCEPTED:
            return False
        if decision.result is not None:
            self._publish_arrival_result(decision.result)
        if decision.kind is DecisionKind.DUPLICATE_PENDING:
            self.get_logger().info("진행 중인 귀가 명령이 중복 수신되어 무시합니다.")
        return True

    def _on_navigate_command(self, msg: String):
        """검증된 서버 이동 명령을 실제 Nav2 임무로 바꿉니다."""
        try:
            command = parse_internal_navigate_command(msg.data)
        except CommandError as exc:
            self.get_logger().error(f"이동 내부 명령 거부: {exc}")
            return

        decision = self._navigate.accept(
            command, self._now_s(), self._ready_for_server_command()
        )

        if decision.kind is not DecisionKind.ACCEPTED:
            if decision.result is not None:
                self._publish_navigate_result(decision.result)
            if decision.kind is DecisionKind.DUPLICATE_PENDING:
                # QoS 1 재전송입니다. 여기서 BUSY 를 보내면 서버가 지금
                # 수행 중인 명령을 실패로 확정해(BUSY 도 종단 상태) 자동
                # 케어 체인이 끊깁니다. 도착했을 때 한 번만 회신합니다.
                self.get_logger().info(
                    f"진행 중인 이동 명령이 중복 수신되어 무시합니다: "
                    f"requestId={command.request_id}"
                )
            return

        self.get_logger().info(
            f"이동 명령: {command.destination} "
            f"(x={command.pose.x}, y={command.pose.y}, yaw={command.pose.yaw}), "
            f"requestId={command.request_id}"
        )
        self._start_navigate_timeout()
        self._start_server_navigation(command)

    def _ready_for_server_command(self):
        """새 이동 명령을 받아도 되는 상태인지.

        스테이션에 대어 놓은 동안(``SERVICING``)도 받아야 합니다. 급수가
        끝나면 서버가 HOME 복귀를 보내는데, 그걸 BUSY 로 막으면 로봇이
        스테이션에서 영영 나오지 못하고 체인이 죽습니다.

        반대로 귀가 마중은 GREETING 에서 사람을 기다리는 동안 상태가
        ``IDLE`` 입니다. 상태만 보면 한가해 보여서, 이동 명령을 받아 마중
        나간 로봇을 끌고 가 버립니다. 그래서 세션을 따로 확인합니다.
        """
        return self._arrival.active is None and self.state in (
            MissionState.IDLE,
            MissionState.SERVICING,
        )

    def _publish_arrival_result(self, result):
        self._publish_result(self._arrival_result_pub, result, "귀가")

    def _publish_navigate_result(self, result):
        self._publish_result(self._navigate_result_pub, result, "이동")

    def _publish_result(self, publisher, result, label):
        publisher.publish(
            String(
                data=result_envelope(
                    result.command_name,
                    result.request_id,
                    result.status,
                    error=result.error,
                    code=result.code,
                )
            )
        )
        self.get_logger().info(
            f"{label} 결과 전달: command={result.command_name}, "
            f"requestId={result.request_id}, status={result.status}"
        )

    def _on_person(self, msg: Bool):
        session = self._arrival.active
        if (
            not msg.data
            or self.state is not MissionState.IDLE
            or session is None
            or session.stage is not ArrivalStage.WAITING_AT_GREETING
        ):
            return
        now = self._now_s()
        if not self.greeting.should_greet(now, person_present=True):
            return

        self.greeting.mark_greeted(now)
        self._transition(MissionState.GREETING)
        self._expression_pub.publish(String(data="VERY_HAPPY"))

        # TODO: 서버 LLM 이 생성한 페르소나 대사로 교체.
        # 지금은 네트워크 없이도 데모가 되도록 고정 문구를 씁니다.
        self._speech_pub.publish(String(data="다녀오셨어요? 오늘도 잘 지냈어요."))
        self._transition(MissionState.IDLE)
        self._begin_return_home("사용자 인식")

    def _start_arrival_timeout(self, seconds):
        if self._arrival_timeout_timer is not None:
            self._arrival_timeout_timer.cancel()
        self._arrival_timeout_timer = self.create_timer(
            float(seconds), self._on_arrival_timeout
        )

    def _start_arrival_wait(self, seconds):
        if self._arrival_wait_timer is not None:
            self._arrival_wait_timer.cancel()
        self._arrival_wait_timer = self.create_timer(
            float(seconds), self._on_arrival_wait_expired
        )

    def _on_arrival_wait_expired(self):
        if self._arrival_wait_timer is not None:
            self._arrival_wait_timer.cancel()
            self._arrival_wait_timer = None
        if self._arrival.waiting_expired(self._now_s()):
            self._begin_return_home("맞이 대기시간 만료")

    def _on_arrival_timeout(self):
        if self._arrival_timeout_timer is not None:
            self._arrival_timeout_timer.cancel()
            self._arrival_timeout_timer = None
        if not self._arrival.total_timeout_expired(self._now_s()):
            return

        session = self._arrival.active
        if session is None:
            return
        if session.stage is ArrivalStage.NAVIGATING_HOME:
            self._cancel_active_navigation()
            result = self._arrival.fail_current(
                "HOME 복귀 제한시간을 초과했습니다.",
                "HOME_TIMEOUT",
            )
            if result is not None:
                self._publish_arrival_result(result)
            self.greeting.disarm()
            self._transition(MissionState.IDLE)
            return
        if session.stage is ArrivalStage.NAVIGATING_GREETING:
            result = self._arrival.fail_current(
                "전체 제한시간 안에 GREETING에 도착하지 못했습니다.",
                "TOTAL_TIMEOUT",
            )
            if result is not None:
                self._publish_arrival_result(result)
        self._begin_return_home("전체 제한시간 만료", reset_timeout=True)

    def _begin_return_home(self, reason, reset_timeout=False):
        session = self._arrival.active
        if session is None:
            return
        self.get_logger().info(f"{reason} -> HOME 복귀")
        self.greeting.disarm()
        if self._arrival_wait_timer is not None:
            self._arrival_wait_timer.cancel()
            self._arrival_wait_timer = None
        if reset_timeout:
            home_timeout = int(
                self.get_parameter("arrival_home_timeout").value
            )
            self._arrival.reset_total_timeout(self._now_s(), home_timeout)
            self._start_arrival_timeout(home_timeout)
        self._arrival.begin_return_home()
        self._cancel_active_navigation()
        self._start_arrival_navigation(
            session.home, purpose=NAV_ARRIVAL_HOME
        )

    def _cancel_arrival_timers(self):
        for name in ("_arrival_wait_timer", "_arrival_timeout_timer"):
            timer = getattr(self, name)
            if timer is not None:
                timer.cancel()
                setattr(self, name, None)

    # --- 이동 ---

    def _start_arrival_navigation(self, map_pose, purpose):
        """서버가 보낸 지도 좌표를 실제 Nav2 목표로 실행합니다."""
        pose = self._map_pose(map_pose)
        if self.get_parameter("skip_navigation").value:
            self.get_logger().warn(
                f"skip_navigation=True - {purpose} Nav2 이동을 시험용으로 건너뜁니다."
            )
            self._handle_nav_success(purpose)
            return
        self._send_nav_goal(pose, purpose=purpose)

    def _start_server_navigation(self, command):
        """서버 command/navigate 의 좌표로 이동합니다."""
        pose = self._map_pose(command.pose)
        if self.get_parameter("skip_navigation").value:
            self.get_logger().warn(
                "skip_navigation=True - 서버 이동을 시험용으로 건너뜁니다."
            )
            self._handle_nav_success(NAV_SERVER)
            return
        self._send_nav_goal(pose, purpose=NAV_SERVER)

    def _send_nav_goal(self, pose, purpose):
        if not self._nav_client.wait_for_server(timeout_sec=2.0):
            self.get_logger().error("Nav2 액션 서버가 없습니다.")
            self._handle_nav_failure(
                purpose, "Nav2 액션 서버에 연결할 수 없습니다.", "NAV2_UNAVAILABLE"
            )
            return

        self._nav_generation += 1
        generation = self._nav_generation
        self._nav_purpose = purpose
        self._transition(MissionState.NAVIGATING)
        goal = NavigateToPose.Goal()
        goal.pose = pose
        self._nav_client.send_goal_async(goal).add_done_callback(
            lambda future: self._on_nav_accepted(
                future, purpose, generation
            )
        )

    def _map_pose(self, map_pose):
        """서버가 보낸 map 좌표를 Nav2 목표로 바꿉니다.

        좌표의 출처는 서버의 ``robot_location`` 하나입니다. 로봇은 위치를
        저장하지 않습니다 — 양쪽이 들고 있으면 지도를 다시 그렸을 때 한쪽만
        갱신되어 엉뚱한 곳으로 갑니다.

        지도 원점(0, 0, 0)도 정상 좌표로 봅니다. 서버는 미설정을 NULL 로
        두고 좌표 없는 위치로는 명령을 아예 발행하지 않으므로, 여기까지 온
        0,0,0 은 "설정 안 됨"이 아니라 진짜 원점입니다.
        """
        x, y, yaw = map_pose.x, map_pose.y, map_pose.yaw
        pose = PoseStamped()
        pose.header.frame_id = "map"
        pose.header.stamp = self.get_clock().now().to_msg()
        pose.pose.position.x = float(x)
        pose.pose.position.y = float(y)
        pose.pose.orientation.z = math.sin(yaw / 2.0)
        pose.pose.orientation.w = math.cos(yaw / 2.0)
        return pose

    def _on_nav_accepted(self, future, purpose, generation):
        handle = future.result()
        if generation != self._nav_generation:
            if handle.accepted:
                handle.cancel_goal_async()
            return
        if not handle.accepted:
            self.get_logger().error("Nav2 가 목표를 거부했습니다.")
            self._handle_nav_failure(
                purpose, "Nav2가 목표를 거부했습니다.", "NAVIGATION_REJECTED"
            )
            return
        self._nav_goal_handle = handle
        handle.get_result_async().add_done_callback(
            lambda result_future: self._on_nav_done(
                result_future, purpose, generation
            )
        )

    def _on_nav_done(self, future, purpose, generation):
        if generation != self._nav_generation:
            return
        self._nav_goal_handle = None
        self._nav_purpose = None

        result = future.result()
        if result.status != 4:  # STATUS_SUCCEEDED
            self.get_logger().error(f"Nav2 이동 실패 (status={result.status})")
            self._handle_nav_failure(
                purpose,
                f"Nav2 이동 실패(status={result.status})",
                "NAVIGATION_FAILED",
            )
            return

        self._handle_nav_success(purpose)

    def _handle_nav_success(self, purpose):
        if purpose == NAV_SERVER:
            session = self._navigate.active
            if session is None:
                # 취소된 명령의 늦은 콜백입니다. 회신할 대상이 없습니다.
                return
            marker_id = self._navigate_marker_id(session.destination)
            if marker_id is None:
                # 마커가 없는 자리입니다. Nav2 도착이 곧 도착입니다.
                self._finish_server_navigation()
                return
            self.get_logger().info(
                f"{session.destination} 근처 도착. 정밀 도킹으로 넘깁니다."
            )
            self._navigate.begin_docking()
            self._start_docking(marker_id)
            return

        if purpose == NAV_ARRIVAL_GREETING:
            result = self._arrival.greeting_reached(self._now_s())
            self._publish_arrival_result(result)
            session = self._arrival.active
            self.get_logger().info("GREETING 도착. 사람을 기다립니다.")
            self._transition(MissionState.IDLE)
            # 무장과 대기 타이머를 같은 자리에서 같은 값으로 겁니다. 둘이
            # 다른 시점에서 시작하면 인사할 수 있는 구간과 문 앞에 서 있는
            # 구간이 어긋납니다.
            self.greeting.arm(self._now_s(), duration=float(session.wait_seconds))
            self._start_arrival_wait(session.wait_seconds)
            return

        if purpose == NAV_ARRIVAL_HOME:
            result = self._arrival.home_reached()
            if result is not None:
                self._publish_arrival_result(result)
            self._cancel_arrival_timers()
            self.greeting.disarm()
            self.get_logger().info("HOME 복귀 완료.")
            self._transition(MissionState.IDLE)

    def _handle_nav_failure(self, purpose, error, code):
        if purpose == NAV_SERVER:
            self._finish_server_navigation(error=error, code=code)
            return

        if purpose == NAV_ARRIVAL_GREETING:
            result = self._arrival.fail_current(error, code)
            if result is not None:
                self._publish_arrival_result(result)
            self._transition(MissionState.IDLE)
            self._begin_return_home("GREETING 이동 실패")
            return

        if purpose == NAV_ARRIVAL_HOME:
            result = self._arrival.fail_current(error, code)
            if result is not None:
                self._publish_arrival_result(result)
            self._cancel_arrival_timers()
            self.greeting.disarm()
            self._transition(MissionState.IDLE)

    # --- 서버 이동 결과 ---

    def _navigate_marker_id(self, destination):
        """도킹이 필요한 목적지면 마커 번호, 아니면 None.

        물리 장치가 있는 곳은 급수 스테이션뿐입니다. 나머지는 지도 위
        좌표일 뿐이라 볼 마커가 없고, Nav2 의 도착 오차로 충분합니다.
        """
        return DOCKING_MARKERS.get(destination)

    def _parked_state(self):
        """도착한 뒤 어떤 상태로 서 있을지.

        급수 스테이션에 서 있는 동안은 ``SERVICING`` 입니다. 서버가
        라즈베리에 급수·송풍·촬영을 시키는 중이고, 서버는 이 상태를 보고
        디스플레이 표정을 매우행복으로 바꿉니다 (DEVICE-MQTT.md 7·9절).
        로봇은 그 작업이 끝났는지 알 방법이 없어서, 다음 이동 명령이 올
        때까지 이 상태를 유지합니다.
        """
        if self._navigate.parked_at == WATER_STATION:
            return MissionState.SERVICING
        return MissionState.IDLE

    def _finish_server_navigation(self, error=None, code=None):
        """이동 명령 하나를 끝내고 서버에 회신합니다.

        ``OK`` 는 출발이 아니라 **도착(도킹 완료)** 을 뜻합니다. 서버는 이
        회신을 받아야 다음 단계(급수·촬영·송풍)를 발행합니다. 제한시간
        (기본 120초)을 넘겼더라도 반드시 보냅니다 — 늦은 회신이
        ``TIMED_OUT`` 위에 덮어쓰고 체인도 이어집니다.

        회신할 세션이 없어도 **상태는 반드시 되돌립니다.** 여기서 그냥
        빠져나가면 NAVIGATING/DOCKING 에 얼어붙어 다음 명령을 전부 BUSY 로
        거절하게 됩니다.
        """
        self._cancel_navigate_timeout()
        if self._navigate.active is None:
            # 이미 정리된 세션의 늦은 콜백입니다. 회신할 대상이 없습니다.
            self._transition(self._parked_state())
            return

        if error is None:
            result = self._navigate.reached()
            self.get_logger().info(
                f"{self._navigate.parked_at} 도착 — 서버에 OK 회신"
            )
        else:
            result = self._navigate.fail_current(error, code)
        if result is not None:
            self._publish_navigate_result(result)
        self._transition(self._parked_state())

    def _start_navigate_timeout(self):
        """Nav2나 도킹이 영영 돌아오지 않을 때를 대비한 마지막 보루.

        결과를 못 받으면 세션이 계속 열려 있어 이후 모든 명령을 BUSY 로
        거절합니다. 노드를 다시 띄우기 전까지 로봇이 서버 명령을 하나도
        받지 못하는 상태가 됩니다.

        서버 제한시간(기본 120초)보다 넉넉히 잡습니다. 늦은 회신도 서버가
        받아 체인을 잇기 때문에, 여기서 먼저 끊으면 살릴 수 있는 이동을
        버리게 됩니다. 이 값은 "정상 범위" 가 아니라 "고장" 판정선입니다.
        """
        self._cancel_navigate_timeout()
        self._navigate_timeout_timer = self.create_timer(
            float(self.get_parameter("navigate_timeout").value),
            self._on_navigate_timeout,
        )

    def _cancel_navigate_timeout(self):
        if self._navigate_timeout_timer is not None:
            self._navigate_timeout_timer.cancel()
            self._navigate_timeout_timer = None

    def _on_navigate_timeout(self):
        self._cancel_navigate_timeout()
        if self._navigate.active is None:
            return
        self.get_logger().error(
            "이동 명령이 제한시간 안에 끝나지 않았습니다. Nav2/도킹 액션이 "
            "응답하는지 확인하세요."
        )
        self._cancel_active_navigation()
        self._finish_server_navigation(
            error="이동이 로봇 제한시간 안에 끝나지 않았습니다.",
            code="NAVIGATION_TIMEOUT",
        )

    def _cancel_active_navigation(self):
        self._nav_generation += 1
        handle, self._nav_goal_handle = self._nav_goal_handle, None
        self._nav_purpose = None
        if handle is not None:
            handle.cancel_goal_async()

    # --- 도킹 ---

    def _start_docking(self, marker_id):
        """마커를 보며 스테이션에 정밀하게 붙습니다.

        서버 이동 명령에서만 씁니다. 도킹하는 목적지는 급수 스테이션
        하나뿐입니다 (navigate_contract.DOCKING_MARKERS).
        """
        if not self._dock_client.wait_for_server(timeout_sec=2.0):
            self.get_logger().error("도킹 액션 서버가 없습니다.")
            self._finish_server_navigation(
                error="도킹 액션 서버에 연결할 수 없습니다.",
                code="DOCKING_UNAVAILABLE",
            )
            return

        self._transition(MissionState.DOCKING)
        goal = DockToStation.Goal()
        goal.marker_id = int(marker_id)
        goal.timeout = float(self.get_parameter("docking_timeout").value)
        self._dock_client.send_goal_async(
            goal, feedback_callback=self._on_dock_feedback
        ).add_done_callback(self._on_dock_accepted)

    def _on_dock_feedback(self, message):
        feedback = message.feedback
        self.get_logger().info(
            f"도킹 {feedback.state} — 거리 {feedback.distance:.2f}m, "
            f"좌우 {feedback.lateral_error:.0f}px, "
            f"각도 {feedback.yaw_error:.1f}도",
            throttle_duration_sec=1.0,
        )

    def _on_dock_accepted(self, future):
        handle = future.result()
        if not handle.accepted:
            self.get_logger().error("도킹 서버가 목표를 거부했습니다.")
            self._finish_server_navigation(
                error="도킹 서버가 목표를 거부했습니다.", code="DOCKING_REJECTED"
            )
            return
        handle.get_result_async().add_done_callback(self._on_dock_done)

    def _on_dock_done(self, future):
        result = future.result().result
        if not result.success:
            self.get_logger().error(f"도킹 실패: {result.message}")
            self._finish_server_navigation(
                error=f"도킹 실패: {result.message}", code="DOCKING_FAILED"
            )
            return

        self.get_logger().info(
            f"도킹 성공 (거리 {result.final_distance:.3f}m, "
            f"각도 {result.final_yaw_error:.1f}도)"
        )
        self._finish_server_navigation()

    # --- 보조 ---

    def _publish_state(self):
        """현재 상태를 주기적으로 다시 알립니다.

        서버 처리가 멱등해서 반복 발행이 안전하고, 전이 시점의 메시지가
        유실돼도 한 주기 안에 복구됩니다.
        """
        self._state_pub.publish(String(data=self.state.name))

    def _transition(self, new_state):
        if new_state is self.state:
            return
        self.get_logger().info(
            f"상태 전이: {self.state.name} -> {new_state.name}"
        )
        self.state = new_state
        self._publish_state()


def main(args=None):
    rclpy.init(args=args)
    node = MissionManager()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
