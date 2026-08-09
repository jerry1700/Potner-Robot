"""simple_navigator — Nav2 없이 좌표로 이동하는 액션 서버.

Nav2 와 **똑같은 액션 이름·타입**(`navigate_to_pose`,
`nav2_msgs/action/NavigateToPose`)을 제공합니다. 그래서 mission_manager 는
Nav2 를 쓸 때와 한 글자도 다르지 않고, 둘 중 어느 쪽을 띄우느냐만
바꾸면 됩니다.

    ros2 launch potner_bringup robot.launch.py          # 이 노드가 뜬다
    ros2 launch potner_bringup nav2.launch.py map:=...  # Nav2 를 쓸 때

★ **둘을 같이 띄우지 마세요.** 같은 액션 이름을 두 서버가 물면 클라이언트가
  아무 쪽에나 붙습니다. robot.launch.py 의 use_simple_nav 를 false 로 두고
  Nav2 를 띄우는 것이 정상 구성입니다.

★ 좌표계 — 이 노드는 **오도메트리 좌표를 그대로 씁니다.** 서버가 보내는
  목표의 frame_id 는 "map" 이지만 지도도 AMCL 도 없으므로 map 과 odom 을
  같은 것으로 봅니다. 그래서 **로봇을 재시작하면 원점이 그 자리로 새로
  잡혀 등록해 둔 좌표가 전부 무의미해집니다.** 바닥에 원점을 표시해 두고
  늘 같은 자리·같은 방향에서 띄우세요.

★ 장애물을 피해 돌아가지 못합니다. 앞을 막으면 safety_node 가 세울 뿐
  입니다. 좁은 시연 공간에서는 Nav2 가 아예 경로를 못 만들기 때문에
  (기본 여유 55cm) 이쪽이 실용적이지만, 넓은 집에서 실운영할 때는
  Nav2 로 가야 합니다.
"""

import math

import rclpy
from geometry_msgs.msg import Twist
from nav2_msgs.action import NavigateToPose
from nav_msgs.msg import Odometry
from rclpy.action import ActionServer, CancelResponse, GoalResponse
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node

from potner_mission.goto_controller import (
    GotoConfig,
    GotoNavigator,
    GotoPhase,
    Pose,
)

CONTROL_PERIOD = 0.05  # 20Hz


class SimpleNavigator(Node):
    def __init__(self):
        super().__init__("simple_navigator")

        self.declare_parameter("cruise_speed", 0.18)
        self.declare_parameter("slow_distance", 0.35)
        self.declare_parameter("min_speed", 0.05)
        self.declare_parameter("kp_heading", 1.2)
        self.declare_parameter("max_angular", 0.8)
        self.declare_parameter("turn_speed", 0.5)
        self.declare_parameter("turn_slow_angle_deg", 45.0)
        self.declare_parameter("turn_min_speed", 0.15)
        self.declare_parameter("position_tolerance", 0.08)
        self.declare_parameter("yaw_tolerance_deg", 5.0)
        self.declare_parameter("navigate_timeout", 60.0)

        self.config = GotoConfig(
            cruise_speed=self.get_parameter("cruise_speed").value,
            slow_distance=self.get_parameter("slow_distance").value,
            min_speed=self.get_parameter("min_speed").value,
            kp_heading=self.get_parameter("kp_heading").value,
            max_angular=self.get_parameter("max_angular").value,
            turn_speed=self.get_parameter("turn_speed").value,
            turn_slow_angle_deg=self.get_parameter("turn_slow_angle_deg").value,
            turn_min_speed=self.get_parameter("turn_min_speed").value,
            position_tolerance=self.get_parameter("position_tolerance").value,
            yaw_tolerance_deg=self.get_parameter("yaw_tolerance_deg").value,
            timeout=self.get_parameter("navigate_timeout").value,
        )

        self._pose = None
        self._busy = False
        self._warned_frame = False

        # 주행 중에도 오도메트리 콜백이 계속 들어와야 합니다. 기본 그룹이면
        # execute 루프가 콜백을 막아 위치가 갱신되지 않고 영원히 제자리로
        # 판단합니다 (도킹 서버에서 같은 문제를 겪었습니다).
        group = ReentrantCallbackGroup()

        self.create_subscription(
            Odometry, "odom", self._on_odom, 10, callback_group=group
        )
        # twist_mux 의 navigation 슬롯입니다. cmd_vel 로 직접 쏘면 안전
        # 정지를 건너뛰고 모터로 직행합니다.
        self._cmd_pub = self.create_publisher(Twist, "cmd_vel_nav", 10)

        self._server = ActionServer(
            self,
            NavigateToPose,
            "navigate_to_pose",
            execute_callback=self._execute,
            goal_callback=self._on_goal,
            cancel_callback=self._on_cancel,
            callback_group=group,
        )

        self._rate = self.create_rate(1.0 / CONTROL_PERIOD)
        self.get_logger().info(
            "simple_navigator 준비 완료 (액션: navigate_to_pose). "
            "오도메트리 원점 기준으로 주행합니다 — 재시작하면 좌표가 바뀝니다."
        )

    # --- 관측 ---

    def _on_odom(self, msg: Odometry):
        q = msg.pose.pose.orientation
        yaw = math.atan2(
            2.0 * (q.w * q.z + q.x * q.y),
            1.0 - 2.0 * (q.y * q.y + q.z * q.z),
        )
        self._pose = Pose(
            msg.pose.pose.position.x, msg.pose.pose.position.y, yaw
        )

    # --- 액션 ---

    def _on_goal(self, goal_request):
        if self._busy:
            self.get_logger().warn("이미 주행 중입니다. 새 목표를 거부합니다.")
            return GoalResponse.REJECT
        if self._pose is None:
            self.get_logger().error(
                "오도메트리가 아직 없습니다. base_driver 가 떴는지 확인하세요."
            )
            return GoalResponse.REJECT
        return GoalResponse.ACCEPT

    def _on_cancel(self, goal_handle):
        self.get_logger().info("주행 취소 요청을 받았습니다.")
        return CancelResponse.ACCEPT

    def _execute(self, goal_handle):
        goal = self._goal_pose(goal_handle.request.pose)
        navigator = GotoNavigator(self.config)

        self._busy = True
        started = self.get_clock().now()
        self.get_logger().info(
            f"주행 시작 -> x={goal.x:.2f} y={goal.y:.2f} "
            f"yaw={math.degrees(goal.yaw):.0f}도"
        )

        try:
            while rclpy.ok():
                if goal_handle.is_cancel_requested:
                    self._stop()
                    goal_handle.canceled()
                    self.get_logger().info("주행을 취소했습니다.")
                    return NavigateToPose.Result()

                elapsed = self._seconds_since(started)
                if elapsed > self.config.timeout:
                    # 바퀴가 헛돌거나 장애물에 막혀 safety 가 계속 세우는
                    # 상황입니다. 계속 명령을 내면 벽을 밀고 있게 됩니다.
                    self._stop()
                    goal_handle.abort()
                    self.get_logger().warn(
                        f"주행 시간 초과 ({self.config.timeout:.0f}초)"
                    )
                    return NavigateToPose.Result()

                step = navigator.step(self._pose, goal)
                self._publish(step)

                if step.phase is GotoPhase.ARRIVED:
                    self._stop()
                    goal_handle.succeed()
                    self.get_logger().info(
                        f"도착 (남은 거리 {step.distance * 100:.0f}cm, "
                        f"{elapsed:.1f}초)"
                    )
                    return NavigateToPose.Result()

                goal_handle.publish_feedback(self._feedback(step, elapsed))
                self._rate.sleep()
        finally:
            self._busy = False
            self._stop()

        return NavigateToPose.Result()

    # --- 보조 ---

    def _goal_pose(self, stamped):
        frame = stamped.header.frame_id
        if frame not in ("map", "odom", "") and not self._warned_frame:
            # 한 번만 알립니다. 매 목표마다 찍으면 로그가 묻힙니다.
            self._warned_frame = True
            self.get_logger().warn(
                f"목표 좌표계가 '{frame}' 입니다. 이 노드는 오도메트리 "
                "좌표로만 주행하므로 그대로 씁니다."
            )
        q = stamped.pose.orientation
        yaw = math.atan2(
            2.0 * (q.w * q.z + q.x * q.y),
            1.0 - 2.0 * (q.y * q.y + q.z * q.z),
        )
        return Pose(stamped.pose.position.x, stamped.pose.position.y, yaw)

    def _seconds_since(self, stamp):
        return (self.get_clock().now() - stamp).nanoseconds * 1e-9

    def _publish(self, step):
        twist = Twist()
        twist.linear.x = step.linear
        twist.angular.z = step.angular
        self._cmd_pub.publish(twist)

    def _stop(self):
        self._cmd_pub.publish(Twist())

    def _feedback(self, step, elapsed):
        feedback = NavigateToPose.Feedback()
        feedback.distance_remaining = float(step.distance)
        feedback.navigation_time.sec = int(elapsed)
        feedback.current_pose.header.frame_id = "odom"
        feedback.current_pose.header.stamp = self.get_clock().now().to_msg()
        feedback.current_pose.pose.position.x = self._pose.x
        feedback.current_pose.pose.position.y = self._pose.y
        feedback.current_pose.pose.orientation.z = math.sin(self._pose.yaw / 2.0)
        feedback.current_pose.pose.orientation.w = math.cos(self._pose.yaw / 2.0)
        return feedback


def main(args=None):
    rclpy.init(args=args)
    node = SimpleNavigator()
    # 액션 실행 루프와 오도메트리 콜백이 동시에 돌아야 합니다. 단일
    # 스레드면 execute 안의 rate.sleep() 이 콜백을 막습니다.
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
