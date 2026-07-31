"""mission_manager — 로봇이 다음에 무엇을 할지 정하는 두뇌.

legacy/main.py 의 우선순위 스케줄러를 옮겼습니다. 가장 큰 차이는
**블로킹하지 않는다**는 점입니다. 기존 코드는 start_docking() 이 끝날
때까지 while 루프가 멈춰 있어서, 도킹 중에는 사용자가 귀가해도 반응할
수 없었습니다. 여기서는 Nav2와 도킹을 액션으로 보내고 결과를 콜백으로
받아, 기다리는 동안에도 다른 이벤트를 처리합니다.

임무 흐름:

    IDLE ─(센서 기준 초과)─> NAVIGATING ─> DOCKING ─> SERVICING ─> IDLE
       └─(welcome_start)─> GREETING 위치 ─(사람/시간 만료)─> HOME ─> IDLE

인사는 아무 때나 하지 않습니다. 서버의 welcome_start가 인사를 무장시키고,
GREETING 도착 뒤 waitSeconds 안에 카메라가 사람을 보면 그때 인사합니다.
welcome_cancel은 진행 중 이동과 대기를 끊고 HOME으로 복귀시킵니다.

Nav2 와 도킹의 역할을 나눈 이유가 있습니다. Nav2 는 지도 좌표까지 잘
데려다주지만 도착 오차가 수십 cm 입니다. 충전 단자를 맞추려면 cm 단위가
필요해서 마지막 구간만 마커를 보는 전용 컨트롤러가 맡습니다.
"""

import math
from enum import Enum, auto

import rclpy
from geometry_msgs.msg import PoseStamped
from nav2_msgs.action import NavigateToPose
from rclpy.action import ActionClient
from rclpy.node import Node
from std_msgs.msg import Bool, Float32, String

from potner_bridge.arrival_contract import (
    ArrivalCommandError,
    WelcomeCancelCommand,
    WelcomeStartCommand,
    parse_internal_arrival_command,
    result_envelope,
)
from potner_msgs.action import DockToStation
from potner_mission.arrival_session import (
    ArrivalSessionController,
    ArrivalStage,
    DecisionKind,
    MissionResult,
)
from potner_mission.greeting import GreetingPolicy
from potner_mission.priority import Readings, StationMarker, Thresholds, evaluate


class MissionState(Enum):
    IDLE = auto()
    NAVIGATING = auto()
    DOCKING = auto()
    SERVICING = auto()
    GREETING = auto()


class MissionManager(Node):
    def __init__(self):
        super().__init__("mission_manager")

        self.declare_parameter("battery_percent", 20.0)
        self.declare_parameter("moisture_percent", 30.0)
        self.declare_parameter("light_lux", 200.0)
        self.declare_parameter("temperature_celsius", 30.0)
        self.declare_parameter("evaluate_period", 2.0)
        self.declare_parameter("greeting_cooldown", 300.0)
        self.declare_parameter("service_duration", 10.0)
        self.declare_parameter("docking_timeout", 90.0)
        self.declare_parameter("arrival_home_timeout", 120.0)
        self.declare_parameter("skip_navigation", False)
        self.declare_parameter("autonomous_missions_enabled", False)

        # 스테이션별 지도 좌표 [x, y, yaw(rad)]
        # TODO: SLAM 으로 지도를 만든 뒤 실제 좌표로 교체할 것.
        #   RViz 에서 스테이션 앞에 커서를 올리고 좌표를 읽으면 됩니다.
        for name in ("charging", "water", "sunlight", "wind"):
            self.declare_parameter(f"station_poses.{name}", [0.0, 0.0, 0.0])

        self.thresholds = Thresholds(
            battery_percent=self.get_parameter("battery_percent").value,
            moisture_percent=self.get_parameter("moisture_percent").value,
            light_lux=self.get_parameter("light_lux").value,
            temperature_celsius=self.get_parameter("temperature_celsius").value,
        )

        self.state = MissionState.IDLE
        self.readings = Readings()
        self.greeting = GreetingPolicy(
            cooldown=self.get_parameter("greeting_cooldown").value,
        )
        self._arrival = ArrivalSessionController()
        self._arrival_wait_timer = None
        self._arrival_timeout_timer = None
        self._active_station = None
        self._service_timer = None
        # Nav2 를 스테이션 이동과 마중 이동이 같이 쓰므로, 도착했을 때
        # 도킹으로 넘길지 제자리 대기로 넘길지를 이걸로 구분합니다.
        self._nav_purpose = None
        self._nav_goal_handle = None
        self._nav_generation = 0

        # 식물 센서 (plant_sensors 노드가 발행)
        self.create_subscription(Float32, "plant/moisture", self._set("moisture"), 10)
        self.create_subscription(Float32, "plant/lux", self._set("light"), 10)
        # ★ 대기 온도는 현재 아무도 발행하지 않아 계속 None 입니다.
        #   스테이션이 재서 서버로 올리는데, 브로커 ACL 이 기기끼리 주고받는
        #   것을 막아 로봇이 그 값을 받을 경로가 없습니다 (DEVICE-MQTT.md 3절).
        #   그래서 송풍(WIND) 임무는 지금 절대 걸리지 않습니다. Readings 가
        #   None 을 건너뛰므로 오작동은 아니고, 서버가 command 로 온도를
        #   내려주기로 정해지면 그때 연결하면 됩니다.
        self.create_subscription(
            Float32, "plant/temperature", self._set("temperature"), 10
        )
        self.create_subscription(Float32, "battery/percent", self._set("battery"), 10)

        # 사람 감지 (person_detector 노드가 발행)
        self.create_subscription(Bool, "perception/person_present", self._on_person, 10)

        # mqtt_bridge가 검증한 실제 서버 welcome_start/cancel 명령.
        self.create_subscription(
            String, "mission/arrival_command", self._on_arrival_command, 10
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
        # 스테이션에 서비스 시작을 요청합니다.
        # ★ 로봇 안에서만 흐릅니다. 예전에는 mqtt_bridge 가 이걸
        #   potner/station/.../request 로 중계했지만 브로커 ACL 이 그 경로를
        #   막습니다. 급수 명령은 서버가 스테이션에 직접 보내는 것으로
        #   정리됐습니다 (DEVICE-MQTT.md 12절). 로봇의 의도를 로그로 남기고
        #   서비스 타이머를 걸기 위해 발행은 유지합니다.
        self._service_pub = self.create_publisher(String, "station/request", 10)

        self._nav_client = ActionClient(self, NavigateToPose, "navigate_to_pose")
        self._dock_client = ActionClient(self, DockToStation, "dock_to_station")

        self.create_timer(self.get_parameter("evaluate_period").value, self._evaluate)
        self.get_logger().info("mission_manager 시작 (상태: IDLE)")

    # --- 입력 ---

    def _set(self, field):
        """Float32 토픽 값을 Readings 의 해당 필드에 넣는 콜백을 만듭니다."""

        def callback(msg):
            setattr(self.readings, field, msg.data)

        return callback

    def _now_s(self) -> float:
        return self.get_clock().now().nanoseconds * 1e-9

    def _on_arrival_command(self, msg: String):
        """검증된 welcome_start/cancel을 실제 Nav2 임무로 바꿉니다."""
        try:
            command = parse_internal_arrival_command(msg.data)
        except ArrivalCommandError as exc:
            self.get_logger().error(f"귀가 내부 명령 거부: {exc}")
            return

        robot_idle = self.state is MissionState.IDLE
        if isinstance(command, WelcomeStartCommand):
            decision = self._arrival.accept_start(
                command, self._now_s(), robot_idle
            )
            if self._finish_decision(decision):
                return
            self.greeting.arm(
                self._now_s(), duration=float(command.wait_seconds)
            )
            self._start_arrival_timeout(command.total_timeout_seconds)
            self.get_logger().info(
                f"귀가 마중 시작: visitId={command.visit_id}, "
                f"GREETING={command.greeting}, HOME={command.home}"
            )
            self._start_arrival_navigation(
                command.greeting, purpose="arrival_greeting"
            )
            return

        if isinstance(command, WelcomeCancelCommand):
            decision = self._arrival.accept_cancel(
                command, self._now_s(), robot_idle
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
                command.home, purpose="arrival_home"
            )

    def _finish_decision(self, decision) -> bool:
        if decision.kind is DecisionKind.ACCEPTED:
            return False
        if decision.result is not None:
            self._publish_arrival_result(decision.result)
        if decision.kind is DecisionKind.DUPLICATE_PENDING:
            self.get_logger().info("진행 중인 귀가 명령이 중복 수신되어 무시합니다.")
        return True

    def _publish_arrival_result(self, result: MissionResult):
        self._arrival_result_pub.publish(
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
            f"귀가 결과 전달: command={result.command_name}, "
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

    def _start_arrival_timeout(self, seconds: int):
        if self._arrival_timeout_timer is not None:
            self._arrival_timeout_timer.cancel()
        self._arrival_timeout_timer = self.create_timer(
            float(seconds), self._on_arrival_timeout
        )

    def _start_arrival_wait(self, seconds: int):
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

    def _begin_return_home(self, reason: str, reset_timeout: bool = False):
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
            session.home, purpose="arrival_home"
        )

    def _cancel_arrival_timers(self):
        for name in ("_arrival_wait_timer", "_arrival_timeout_timer"):
            timer = getattr(self, name)
            if timer is not None:
                timer.cancel()
                setattr(self, name, None)

    # --- 판단 ---

    def _evaluate(self):
        self._state_pub.publish(String(data=self.state.name))

        if self.state is not MissionState.IDLE:
            return

        if self._arrival.active is not None:
            return

        # 서버가 NAVIGATE와 후속 장치 명령을 담당하므로 운영 기본값은 false입니다.
        # 예전 Jetson 단독 임무 판단을 별도로 시험할 때만 true로 바꿉니다.
        if not self.get_parameter("autonomous_missions_enabled").value:
            return

        station = evaluate(self.readings, self.thresholds)
        if station is not None:
            self.get_logger().info(f"임무 발생: {station.name} 스테이션으로 이동")
            self._active_station = station
            self._start_navigation(station)

    # --- 이동 ---

    def _start_navigation(self, station: StationMarker):
        if self.get_parameter("skip_navigation").value:
            # 지도가 아직 없을 때 도킹만 따로 시험하기 위한 우회 경로입니다.
            self.get_logger().warn("skip_navigation=True — 이동을 건너뛰고 도킹합니다.")
            self._start_docking(station)
            return

        pose_name = f"station_poses.{station.name.lower()}"
        pose = self._pose_from(self.get_parameter(pose_name).value)
        if pose is None:
            self.get_logger().error(
                f"{station.name} 스테이션 좌표가 설정되지 않았습니다. "
                "station_poses 를 채우세요."
            )
            self._abort()
            return

        self._send_nav_goal(pose, purpose="station")

    def _start_arrival_navigation(self, map_pose, purpose: str):
        """서버가 보낸 지도 좌표를 실제 Nav2 목표로 실행합니다."""
        pose = self._pose_from_values(
            [map_pose.x, map_pose.y, map_pose.yaw], allow_origin=True
        )
        if self.get_parameter("skip_navigation").value:
            self.get_logger().warn(
                f"skip_navigation=True - {purpose} Nav2 이동을 시험용으로 건너뜁니다."
            )
            self._handle_nav_success(purpose)
            return
        self._send_nav_goal(pose, purpose=purpose)

    def _send_nav_goal(self, pose: PoseStamped, purpose: str):
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

    def _pose_from(self, values):
        """[x, y, yaw] 파라미터를 지도 좌표 자세로 바꿉니다.

        좌표가 전부 0 이면 아직 설정되지 않은 것으로 봅니다. 그대로
        보내면 로봇이 지도 원점으로 달려갑니다.
        """
        return self._pose_from_values(values, allow_origin=False)

    def _pose_from_values(self, values, allow_origin: bool):
        if values is None or len(values) < 3:
            return None
        x, y, yaw = values[0], values[1], values[2]
        if not allow_origin and x == 0.0 and y == 0.0 and yaw == 0.0:
            return None

        pose = PoseStamped()
        pose.header.frame_id = "map"
        pose.header.stamp = self.get_clock().now().to_msg()
        pose.pose.position.x = float(x)
        pose.pose.position.y = float(y)
        pose.pose.orientation.z = math.sin(yaw / 2.0)
        pose.pose.orientation.w = math.cos(yaw / 2.0)
        return pose

    def _on_nav_accepted(self, future, purpose: str, generation: int):
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

    def _on_nav_done(self, future, purpose: str, generation: int):
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

    def _handle_nav_success(self, purpose: str):
        if purpose == "arrival_greeting":
            result = self._arrival.greeting_reached(self._now_s())
            self._publish_arrival_result(result)
            session = self._arrival.active
            self.get_logger().info("GREETING 도착. 사람을 기다립니다.")
            self._transition(MissionState.IDLE)
            self._start_arrival_wait(session.wait_seconds)
            return

        if purpose == "arrival_home":
            result = self._arrival.home_reached()
            if result is not None:
                self._publish_arrival_result(result)
            self._cancel_arrival_timers()
            self.greeting.disarm()
            self.get_logger().info("HOME 복귀 완료.")
            self._transition(MissionState.IDLE)
            return

        self.get_logger().info("스테이션 근처 도착. 정밀 도킹으로 넘깁니다.")
        self._start_docking(self._active_station)

    def _handle_nav_failure(self, purpose: str, error: str, code: str):
        if purpose == "station":
            self._abort()
            return

        if purpose == "arrival_greeting":
            result = self._arrival.fail_current(error, code)
            if result is not None:
                self._publish_arrival_result(result)
            self._transition(MissionState.IDLE)
            self._begin_return_home("GREETING 이동 실패")
            return

        if purpose == "arrival_home":
            result = self._arrival.fail_current(error, code)
            if result is not None:
                self._publish_arrival_result(result)
            self._cancel_arrival_timers()
            self.greeting.disarm()
            self._transition(MissionState.IDLE)

    def _cancel_active_navigation(self):
        self._nav_generation += 1
        handle, self._nav_goal_handle = self._nav_goal_handle, None
        self._nav_purpose = None
        if handle is not None:
            handle.cancel_goal_async()

    # --- 도킹 ---

    def _start_docking(self, station: StationMarker):
        if not self._dock_client.wait_for_server(timeout_sec=2.0):
            self.get_logger().error("도킹 액션 서버가 없습니다.")
            self._abort()
            return

        self._transition(MissionState.DOCKING)
        goal = DockToStation.Goal()
        goal.marker_id = int(station)
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
            self._abort()
            return
        handle.get_result_async().add_done_callback(self._on_dock_done)

    def _on_dock_done(self, future):
        result = future.result().result
        if not result.success:
            self.get_logger().error(f"도킹 실패: {result.message}")
            self._abort()
            return

        self.get_logger().info(
            f"도킹 성공 (거리 {result.final_distance:.3f}m, "
            f"각도 {result.final_yaw_error:.1f}도)"
        )
        self._start_service()

    # --- 서비스 ---

    def _start_service(self):
        """스테이션에 급수·송풍 등을 요청하고 완료를 기다립니다."""
        self._transition(MissionState.SERVICING)
        self._service_pub.publish(String(data=self._active_station.name))

        # TODO: 스테이션이 완료 신호를 보내면 그걸 받아 끝내도록 바꿀 것.
        # 지금은 스테이션이 없으므로 고정 시간 대기로 둡니다.
        duration = self.get_parameter("service_duration").value
        self._service_timer = self.create_timer(duration, self._finish_service)

    def _finish_service(self):
        if self._service_timer is not None:
            self._service_timer.cancel()
            self._service_timer = None
        self.get_logger().info(f"{self._active_station.name} 서비스 완료")
        self._active_station = None
        self._transition(MissionState.IDLE)

    # --- 보조 ---

    def _abort(self):
        self._active_station = None
        self._transition(MissionState.IDLE)

    def _transition(self, new_state: MissionState):
        if new_state is self.state:
            return
        self.get_logger().info(
            f"상태 전이: {self.state.name} -> {new_state.name}"
        )
        self.state = new_state
        self._state_pub.publish(String(data=self.state.name))


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
