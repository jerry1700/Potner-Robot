"""mission_manager — 로봇이 다음에 무엇을 할지 정하는 두뇌.

legacy/main.py 의 우선순위 스케줄러를 옮겼습니다. 가장 큰 차이는
**블로킹하지 않는다**는 점입니다. 기존 코드는 start_docking() 이 끝날
때까지 while 루프가 멈춰 있어서, 도킹 중에는 사용자가 귀가해도 반응할
수 없었습니다. 여기서는 Nav2와 도킹을 액션으로 보내고 결과를 콜백으로
받아, 기다리는 동안에도 다른 이벤트를 처리합니다.

임무 흐름:

    IDLE ─(센서 기준 초과)─> NAVIGATING ─> DOCKING ─> SERVICING ─> IDLE
       └─(귀가 알림)─> [맞이 위치로 이동] ─(사람 감지)─> GREETING ─> IDLE

인사는 아무 때나 하지 않습니다. 앱의 귀가 알림(서버 command/greet)이
인사를 무장시키고, 무장된 시간창 안에 카메라가 사람을 보면 그때 인사합니다.
판단 로직은 potner_mission.greeting 에 있습니다.

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

from potner_msgs.action import DockToStation
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
        self.declare_parameter("greet_arm_duration", 600.0)
        self.declare_parameter("service_duration", 10.0)
        self.declare_parameter("docking_timeout", 90.0)
        self.declare_parameter("skip_navigation", False)

        # 스테이션별 지도 좌표 [x, y, yaw(rad)]
        # TODO: SLAM 으로 지도를 만든 뒤 실제 좌표로 교체할 것.
        #   RViz 에서 스테이션 앞에 커서를 올리고 좌표를 읽으면 됩니다.
        for name in ("charging", "water", "sunlight", "wind"):
            self.declare_parameter(f"station_poses.{name}", [0.0, 0.0, 0.0])

        # 맞이 위치 — 현관이 보이는 지점 [x, y, yaw(rad)].
        # 전부 0 이면 미설정으로 보고 이동 없이 제자리에서 무장만 합니다.
        # 지도가 생기기 전에도 기능의 나머지가 다 돌게 하기 위한 것입니다.
        self.declare_parameter("greet_pose", [0.0, 0.0, 0.0])

        self.thresholds = Thresholds(
            battery_percent=self.get_parameter("battery_percent").value,
            moisture_percent=self.get_parameter("moisture_percent").value,
            light_lux=self.get_parameter("light_lux").value,
            temperature_celsius=self.get_parameter("temperature_celsius").value,
        )

        self.state = MissionState.IDLE
        self.readings = Readings()
        self.greeting = GreetingPolicy(
            arm_duration=self.get_parameter("greet_arm_duration").value,
            cooldown=self.get_parameter("greeting_cooldown").value,
        )
        self._active_station = None
        self._service_timer = None
        # Nav2 를 스테이션 이동과 마중 이동이 같이 쓰므로, 도착했을 때
        # 도킹으로 넘길지 제자리 대기로 넘길지를 이걸로 구분합니다.
        self._nav_purpose = None

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

        # 귀가 알림 (앱 지오펜스 -> 서버 command/greet -> mqtt_bridge 가 중계)
        self.create_subscription(String, "mission/greet_command", self._on_greet_command, 10)

        self._speech_pub = self.create_publisher(String, "tts/say", 10)
        # 인사하는 순간의 표정. 평소 표정은 서버가 정하지만(30초 주기 반복),
        # 인사는 로봇이 시작하는 이벤트라 즉시 반응이 필요합니다. 서버 주기가
        # 돌아오면 서버 값으로 자연히 덮입니다.
        self._expression_pub = self.create_publisher(String, "display/expression", 10)
        self._state_pub = self.create_publisher(String, "mission/state", 10)
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

    def _on_greet_command(self, msg: String):
        """앱의 귀가 알림. 인사를 무장하고 맞이 위치로 이동합니다.

        임무 수행 중(급수 이동 등)이면 이동은 하지 않고 무장만 합니다.
        임무가 끝나고 시간창 안에 사람이 보이면 그 자리에서라도 인사합니다.
        마중이 늦는 것이 급수를 버리는 것보다 낫습니다.
        """
        self.greeting.arm(self._now_s())
        self.get_logger().info(
            f"귀가 알림 수신 — {self.greeting.arm_duration:.0f}초간 인사 대기"
        )

        if self.state is MissionState.IDLE:
            self._start_greet_navigation()

    def _on_person(self, msg: Bool):
        if not msg.data or self.state is not MissionState.IDLE:
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

    # --- 판단 ---

    def _evaluate(self):
        self._state_pub.publish(String(data=self.state.name))

        if self.state is not MissionState.IDLE:
            return

        # 인사 대기 중에는 새 임무를 시작하지 않습니다. 사용자가 곧 문을
        # 여는데 로봇이 급수하러 떠나면 마중이라는 기능 자체가 무너집니다.
        # 시간창(기본 10분)이 짧아서 임무가 크게 밀리지도 않습니다.
        if self.greeting.is_armed(self._now_s()):
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

        pose = self._pose_from(self.get_parameter(f"station_poses.{station.name.lower()}").value)
        if pose is None:
            self.get_logger().error(
                f"{station.name} 스테이션 좌표가 설정되지 않았습니다. "
                "station_poses 를 채우세요."
            )
            self._abort()
            return

        self._send_nav_goal(pose, purpose="station")

    def _start_greet_navigation(self):
        """맞이 위치로 이동합니다. 좌표가 없으면 제자리에서 무장만 합니다.

        스테이션 이동과 달리 좌표 미설정이 에러가 아닙니다 — 지도가 생기기
        전에도 "무장 -> 사람 인식 -> 인사"까지는 다 돌아야 하기 때문입니다.
        """
        pose = self._pose_from(self.get_parameter("greet_pose").value)
        if pose is None or self.get_parameter("skip_navigation").value:
            self.get_logger().info("맞이 위치 미설정 — 제자리에서 인사를 기다립니다.")
            return

        self._send_nav_goal(pose, purpose="greet")

    def _send_nav_goal(self, pose: PoseStamped, purpose: str):
        if not self._nav_client.wait_for_server(timeout_sec=2.0):
            self.get_logger().error("Nav2 액션 서버가 없습니다.")
            if purpose == "station":
                self._abort()
            # 마중 이동 실패는 임무 실패가 아닙니다. 제자리에서 기다립니다.
            return

        self._nav_purpose = purpose
        self._transition(MissionState.NAVIGATING)
        goal = NavigateToPose.Goal()
        goal.pose = pose
        self._nav_client.send_goal_async(goal).add_done_callback(self._on_nav_accepted)

    def _pose_from(self, values):
        """[x, y, yaw] 파라미터를 지도 좌표 자세로 바꿉니다.

        좌표가 전부 0 이면 아직 설정되지 않은 것으로 봅니다. 그대로
        보내면 로봇이 지도 원점으로 달려갑니다.
        """
        if values is None or len(values) < 3:
            return None
        x, y, yaw = values[0], values[1], values[2]
        if x == 0.0 and y == 0.0 and yaw == 0.0:
            return None

        pose = PoseStamped()
        pose.header.frame_id = "map"
        pose.header.stamp = self.get_clock().now().to_msg()
        pose.pose.position.x = float(x)
        pose.pose.position.y = float(y)
        pose.pose.orientation.z = math.sin(yaw / 2.0)
        pose.pose.orientation.w = math.cos(yaw / 2.0)
        return pose

    def _on_nav_accepted(self, future):
        handle = future.result()
        if not handle.accepted:
            self.get_logger().error("Nav2 가 목표를 거부했습니다.")
            self._abort()
            return
        handle.get_result_async().add_done_callback(self._on_nav_done)

    def _on_nav_done(self, future):
        purpose, self._nav_purpose = self._nav_purpose, None

        # Nav2 의 상세 실패 원인까지 구분하지 않고, 도착 실패면 임무를
        # 접습니다. 재시도 정책은 실주행 데이터를 본 뒤 정하는 게 낫습니다.
        result = future.result()
        if result.status != 4:  # STATUS_SUCCEEDED
            self.get_logger().error(f"Nav2 이동 실패 (status={result.status})")
            # 마중은 이동에 실패해도 무장을 유지합니다. 그 자리에서라도
            # 사람이 보이면 인사하는 편이 아무것도 안 하는 것보다 낫습니다.
            self._abort()
            return

        if purpose == "greet":
            self.get_logger().info("맞이 위치 도착. 사람을 기다립니다.")
            self._transition(MissionState.IDLE)
            return

        self.get_logger().info("스테이션 근처 도착. 정밀 도킹으로 넘깁니다.")
        self._start_docking(self._active_station)

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
