"""docking_server — 스테이션 정밀 도킹 액션 서버.

Nav2 가 스테이션 앞까지 데려다준 뒤 최종 접근을 맡습니다. Nav2 는 지도
좌표까지는 잘 데려다주지만 도착 오차가 수십 cm 라, 급수 노즐과 카메라를 맞추려면
마커를 보는 전용 제어가 필요합니다.

모터 없이 검증하는 방법:

    터미널 1: ros2 launch potner_bringup robot.launch.py use_lidar:=false
    터미널 2: ros2 topic echo /cmd_vel_docking
    터미널 3: ros2 action send_goal /dock_to_station \\
                  potner_msgs/action/DockToStation "{marker_id: 2}" --feedback

마커를 좌우로 옮기면 angular.z 부호가 바뀌고, 가까이 대면 linear.x 가
0 으로 떨어집니다. 모터가 없어도 제어 루프 전체를 확인할 수 있습니다.

주의 — 스테이션 초음파(HC-SR04)를 제어 루프에 넣지 마세요. 그 값은
스테이션 ESP32 -> 인터넷 -> EC2 브로커 -> 로봇 경로로 오기 때문에 왕복
지연이 수백 ms 에 지터까지 있습니다. 이 노드는 로봇에 달린 카메라만
보고 제어하고, 스테이션 신호는 완료 판정에만 씁니다.
"""

import math

import rclpy
from geometry_msgs.msg import Twist, Vector3
from nav_msgs.msg import Odometry
from rclpy.action import ActionServer, CancelResponse, GoalResponse
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import LaserScan
from std_msgs.msg import Bool, Int32, String

from potner_docking.approach_controller import DockingGains
from potner_docking.marker_search import SearchConfig, front_clearance
from potner_docking.session import DockingSession, SessionLimits
from potner_docking.turn_tracker import TurnAccumulator, yaw_from_quaternion
from potner_msgs.action import DockToStation

CONTROL_PERIOD = 0.05  # 20Hz


class DockingServer(Node):
    def __init__(self):
        super().__init__("docking_server")

        self.declare_parameter("kp_lateral", 0.0025)
        self.declare_parameter("kp_yaw", 0.017)
        self.declare_parameter("approach_speed", 0.12)
        self.declare_parameter("max_angular", 0.8)
        self.declare_parameter("target_distance", 0.15)
        self.declare_parameter("marker_lost_timeout", 2.0)
        self.declare_parameter("docking_timeout", 90.0)
        self.declare_parameter("confirm_timeout", 5.0)
        self.declare_parameter("search_timeout", 0.0)
        self.declare_parameter("turn_after_dock_deg", 180.0)
        self.declare_parameter("turn_speed", 0.5)
        self.declare_parameter("turn_slow_angle_deg", 60.0)
        self.declare_parameter("turn_min_speed", 0.15)
        self.declare_parameter("turn_stop_margin_deg", 2.0)
        self.declare_parameter("turn_timeout", 20.0)
        self.declare_parameter("align_timeout", 15.0)
        self.declare_parameter("aim_offset_px_per_deg", 6.0)
        self.declare_parameter("aim_offset_distance", 0.55)
        self.declare_parameter("aim_offset_max_px", 120.0)
        self.declare_parameter("edge_guard_px", 170.0)
        self.declare_parameter("edge_min_speed_ratio", 0.25)
        self.declare_parameter("search_turn_speed", 0.4)
        self.declare_parameter("search_sweep_deg", 360.0)
        self.declare_parameter("search_step_deg", 20.0)
        self.declare_parameter("search_pause_time", 1.0)
        self.declare_parameter("search_creep_speed", 0.06)
        self.declare_parameter("search_creep_distance", 0.15)
        self.declare_parameter("search_front_clear", 0.50)
        self.declare_parameter("require_station_confirm", False)

        self.gains = DockingGains(
            kp_lateral=self.get_parameter("kp_lateral").value,
            kp_yaw=self.get_parameter("kp_yaw").value,
            approach_speed=self.get_parameter("approach_speed").value,
            max_angular=self.get_parameter("max_angular").value,
            target_distance=self.get_parameter("target_distance").value,
            turn_after_dock_deg=self.get_parameter("turn_after_dock_deg").value,
            turn_speed=self.get_parameter("turn_speed").value,
            turn_slow_angle_deg=self.get_parameter("turn_slow_angle_deg").value,
            turn_min_speed=self.get_parameter("turn_min_speed").value,
            turn_stop_margin_deg=self.get_parameter("turn_stop_margin_deg").value,
            aim_offset_px_per_deg=self.get_parameter("aim_offset_px_per_deg").value,
            aim_offset_distance=self.get_parameter("aim_offset_distance").value,
            aim_offset_max_px=self.get_parameter("aim_offset_max_px").value,
            edge_guard_px=self.get_parameter("edge_guard_px").value,
            edge_min_speed_ratio=self.get_parameter("edge_min_speed_ratio").value,
        )
        self.search = SearchConfig(
            turn_speed=self.get_parameter("search_turn_speed").value,
            sweep_angle_deg=self.get_parameter("search_sweep_deg").value,
            step_angle_deg=self.get_parameter("search_step_deg").value,
            pause_time=self.get_parameter("search_pause_time").value,
            creep_speed=self.get_parameter("search_creep_speed").value,
            creep_distance=self.get_parameter("search_creep_distance").value,
            front_clear_m=self.get_parameter("search_front_clear").value,
        )
        self.limits = SessionLimits(
            marker_lost_timeout=self.get_parameter("marker_lost_timeout").value,
            docking_timeout=self.get_parameter("docking_timeout").value,
            confirm_timeout=self.get_parameter("confirm_timeout").value,
            search_timeout=self.get_parameter("search_timeout").value,
            turn_timeout=self.get_parameter("turn_timeout").value,
            align_timeout=self.get_parameter("align_timeout").value,
            require_station_confirm=self.get_parameter(
                "require_station_confirm"
            ).value,
        )

        # 인지 노드가 보내온 최신 관측
        self._visible_id = -1
        self._target_id = -1
        self._pose = None
        self._last_seen = None
        self._station_confirmed = False
        # 도킹 후 제자리 회전량. 오도메트리 yaw 를 누적해서 잽니다.
        self._yaw = None
        self._position = None
        self._creep_origin = None
        self._turn = TurnAccumulator()
        # 탐색 중 전진해도 되는지 판단할 정면 거리. 라이다가 유일한 눈입니다
        # — 범퍼 ToF 는 펌웨어가 최대값으로 고정해 두어 동작하지 않습니다.
        self._scan = None

        # 액션 실행 중에도 구독 콜백이 계속 들어와야 하므로 재진입 그룹을
        # 씁니다. 기본 그룹이면 execute 루프가 콜백을 막아 마커 관측이
        # 갱신되지 않고 영원히 "마커 유실" 상태가 됩니다.
        group = ReentrantCallbackGroup()

        self.create_subscription(
            Vector3, "perception/marker_pose", self._on_pose,
            qos_profile_sensor_data, callback_group=group,
        )
        self.create_subscription(
            Int32, "perception/marker_id", self._on_id,
            qos_profile_sensor_data, callback_group=group,
        )
        # ★ 스테이션의 홀 센서 접점 신호. 현재 아무도 발행하지 않습니다.
        #   브로커 ACL 이 기기끼리 주고받는 것을 막아 스테이션 신호가 로봇까지
        #   오지 못합니다 (DEVICE-MQTT.md 3절). require_station_confirm 이
        #   false 라 도킹은 카메라 정렬만으로 성공 판정합니다. 이 값을 true 로
        #   바꾸면 도킹이 영원히 끝나지 않습니다.
        self.create_subscription(
            Bool, "station/docked", self._on_station, 10, callback_group=group,
        )
        # 회전량 판정용. 마커는 등을 돌리는 순간 안 보이므로 카메라로는
        # 각도를 잴 수 없습니다.
        self.create_subscription(
            Odometry, "odom", self._on_odom, 10, callback_group=group,
        )
        self.create_subscription(
            LaserScan, "scan", self._on_scan, qos_profile_sensor_data,
            callback_group=group,
        )

        self._cmd_pub = self.create_publisher(Twist, "cmd_vel_docking", 10)
        self._phase_pub = self.create_publisher(String, "docking/phase", 10)

        self._server = ActionServer(
            self,
            DockToStation,
            "dock_to_station",
            execute_callback=self._execute,
            goal_callback=self._on_goal,
            cancel_callback=self._on_cancel,
            callback_group=group,
        )

        self._busy = False
        # rate 는 노드에 등록되는 자원이라 목표마다 새로 만들면 쌓입니다.
        self._rate = self.create_rate(1.0 / CONTROL_PERIOD)
        self.get_logger().info("docking_server 준비 완료 (액션: dock_to_station)")

    # --- 관측 ---

    def _on_id(self, msg: Int32):
        self._visible_id = msg.data
        # 목표 마커를 실제로 본 순간만 기록합니다. 다른 마커가 보이는 것을
        # "봤다"로 치면 엉뚱한 스테이션으로 붙습니다.
        if msg.data == self._target_id:
            self._last_seen = self.get_clock().now()

    def _on_pose(self, msg: Vector3):
        # x=거리(m), y=좌우오차(px), z=기울기(deg)
        self._pose = (msg.x, msg.y, msg.z)

    def _on_odom(self, msg: Odometry):
        q = msg.pose.pose.orientation
        self._yaw = yaw_from_quaternion(q.x, q.y, q.z, q.w)
        self._position = (msg.pose.pose.position.x, msg.pose.pose.position.y)

    def _on_scan(self, msg: LaserScan):
        self._scan = msg

    def _front_range(self):
        if self._scan is None:
            return math.inf
        return front_clearance(
            self._scan.ranges,
            self._scan.angle_min,
            self._scan.angle_increment,
            self._scan.range_min,
            self._scan.range_max,
        )

    def _crept(self):
        if self._position is None or self._creep_origin is None:
            return 0.0
        return math.hypot(
            self._position[0] - self._creep_origin[0],
            self._position[1] - self._creep_origin[1],
        )

    def _on_station(self, msg: Bool):
        """스테이션 A3144 홀 센서가 로봇 자석을 감지했다는 신호.

        이산 신호라 네트워크 지연이 문제되지 않고, 카메라보다 확실한
        물리적 접촉 증거입니다.
        """
        self._station_confirmed = msg.data

    # --- 액션 ---

    def _on_goal(self, goal_request):
        if self._busy:
            self.get_logger().warn("이미 도킹 중입니다. 새 목표를 거부합니다.")
            return GoalResponse.REJECT
        if goal_request.marker_id < 0:
            self.get_logger().warn("마커 번호가 유효하지 않습니다.")
            return GoalResponse.REJECT
        return GoalResponse.ACCEPT

    def _on_cancel(self, goal_handle):
        self.get_logger().info("도킹 취소 요청을 받았습니다.")
        return CancelResponse.ACCEPT

    def _execute(self, goal_handle):
        marker_id = goal_handle.request.marker_id
        timeout = goal_handle.request.timeout

        limits = SessionLimits(
            marker_lost_timeout=self.limits.marker_lost_timeout,
            docking_timeout=timeout if timeout > 0.0 else self.limits.docking_timeout,
            confirm_timeout=self.limits.confirm_timeout,
            search_timeout=self.limits.search_timeout,
            turn_timeout=self.limits.turn_timeout,
            align_timeout=self.limits.align_timeout,
            require_station_confirm=self.limits.require_station_confirm,
        )
        session = DockingSession(self.gains, limits)

        self._busy = True
        self._station_confirmed = False
        self._turn.reset()
        self._creep_origin = self._position
        self._target_id = marker_id
        self._last_seen = None
        started = self.get_clock().now()

        self.get_logger().info(f"도킹 시작 (마커 {marker_id})")

        try:
            while rclpy.ok():
                if goal_handle.is_cancel_requested:
                    self._stop()
                    goal_handle.canceled()
                    return self._result(False, "사용자 취소", session)

                elapsed = self._seconds_since(started)
                observation = self._pose if self._visible_id == marker_id else None
                marker_age = (
                    self._seconds_since(self._last_seen)
                    if self._last_seen is not None
                    else math.inf
                )

                # ★ 누적기 갱신은 step 앞에서 합니다. 뒤에서 하면 회전 문턱
                #   판정이 항상 한 주기(0.05s) 낡은 각도를 봅니다 — 0.5rad/s
                #   면 1.4도로, 정지 마진(2도)과 같은 자릿수입니다.
                if self._yaw is not None:
                    self._turn.update(self._yaw)

                previous = session.phase
                step = session.step(
                    elapsed,
                    marker_age,
                    observation,
                    self._station_confirmed,
                    turn_progress=self._turn.turned,
                    creep_progress=self._crept(),
                    front_range=self._front_range(),
                )

                # 단계가 바뀌었거나 탐색이 요청하면 누적기를 되돌립니다.
                # 이전 단계에서 쌓인 값이 섞이면 들어오자마자 "다 돌았다" 로
                # 오판합니다.
                if step.phase is not previous or step.restart_odometry:
                    self._turn.reset()
                    self._creep_origin = self._position
                self._publish(step)

                if step.finished:
                    self._stop()
                    if step.succeeded:
                        goal_handle.succeed()
                    else:
                        goal_handle.abort()
                    return self._result(step.succeeded, step.reason, session)

                goal_handle.publish_feedback(self._feedback(step, session))
                self._rate.sleep()
        finally:
            self._busy = False
            self._target_id = -1
            self._stop()

        return self._result(False, "노드 종료", session)

    # --- 보조 ---

    def _seconds_since(self, stamp):
        return (self.get_clock().now() - stamp).nanoseconds * 1e-9

    def _publish(self, step):
        twist = Twist()
        twist.linear.x = step.linear
        twist.angular.z = step.angular
        self._cmd_pub.publish(twist)
        self._phase_pub.publish(String(data=step.phase.value))

    def _stop(self):
        self._cmd_pub.publish(Twist())

    def _feedback(self, step, session):
        feedback = DockToStation.Feedback()
        feedback.state = step.phase.value
        # 이번 주기의 관측만 담습니다. 옛 값을 계속 보내면 마커를 놓친
        # 상태에서도 숫자가 그대로 떠서 정상 동작으로 오해합니다.
        if step.observation is not None:
            distance, lateral, yaw = step.observation
            feedback.distance = float(distance)
            feedback.lateral_error = float(lateral)
            feedback.yaw_error = float(yaw)
        return feedback

    def _result(self, success, message, session):
        result = DockToStation.Result()
        result.success = success
        result.message = message
        if session.last_observation is not None:
            distance, _lateral, yaw = session.last_observation
            result.final_distance = float(distance)
            result.final_yaw_error = float(yaw)
        level = self.get_logger().info if success else self.get_logger().warn
        level(f"도킹 종료: {message}")
        return result


def main(args=None):
    rclpy.init(args=args)
    node = DockingServer()
    # 액션 실행 루프와 구독 콜백이 동시에 돌아야 하므로 멀티스레드 실행기가
    # 필요합니다. 단일 스레드면 execute 안에서 rate.sleep() 이 콜백을 막습니다.
    executor = MultiThreadedExecutor()
    try:
        rclpy.spin(node, executor=executor)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
