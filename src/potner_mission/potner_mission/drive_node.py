"""drive_node — 휴대폰 방향 버튼(수동 주행)을 실제 움직임으로 바꾸는 곳.

mission_manager 의 자율주행 상태머신(IDLE/NAVIGATING/DOCKING/...)과는 무관한
별도 관심사라 노드를 분리했습니다. 명령은 twist_mux 의 teleop 슬롯(우선순위
200 — Nav2 100보다 높고 safety 255보다 낮음, config/twist_mux.yaml)으로
흘려보냅니다.

버튼 한 번이 늘 같은 양을 움직입니다 — **전진·후진은 step_distance_m
(기본 0.15m), 좌/우 회전은 turn_angle_deg(기본 22.5도)**. 서버가 준
durationMs 는 쓰지 않습니다. 그대로 쓰면 가속에만 시간을 다 쓰고 실측
2~3cm 밖에 못 가서 좌표 등록용으로 쓸 수 없었습니다. 22.5도면 열여섯 번에
한 바퀴라 방향을 눈으로 가늠하기 좋습니다.

★ 목표량 판정은 **시간이 아니라 오도메트리**로 합니다. 시간으로 끊었더니
  22.5도 명령에 29.9도(+33%)를 돌았습니다 — 피드포워드가 목표 속도를 조금
  넘기고, PID 가 그걸 깎아내기 전에 시간이 끝나기 때문입니다. 오도메트리를
  보면 그 오차가 판정에서 사라집니다.

  오도메트리가 안 오거나 바퀴가 헛돌면 목표량이 영원히 안 채워지므로,
  시간 상한(progress_timeout_factor)을 반드시 함께 걸어 둡니다. 상한에
  걸리면 경고를 남깁니다 — 그 로그 자체가 "안 움직이고 있다"는 진단입니다.

★ 유지 중에 딱 한 번만 발행하고 가만히 있으면 안 됩니다. twist_mux 의
  teleop 타임아웃(0.5초)보다 오래 조용하면 twist_mux 가 먼저 "이 입력이
  죽었다"고 보고 끊어 버립니다. 그래서 짧은 주기로 계속 재발행합니다.

drive는 회신(``result/drive``) 계약이 없습니다 — 서버의
``CommandResultTopicParser`` 가 water/capture/fan/navigate 만 인식해서,
보내도 조용히 버려집니다.
"""

import math

import rclpy
from geometry_msgs.msg import Twist
from nav_msgs.msg import Odometry
from rclpy.duration import Duration
from rclpy.node import Node
from std_msgs.msg import String

from potner_bridge.command_result import CommandError
from potner_bridge.drive_contract import hold_seconds, parse_internal_drive_command
from potner_mission.drive_progress import (
    straight_progress,
    turn_progress,
    yaw_from_quaternion,
)

# 재발행 주기. twist_mux 의 teleop 타임아웃(0.5초)보다 확실히 짧아야 유지
# 중에 twist_mux 가 이 입력을 죽은 것으로 보고 먼저 끊지 않습니다. 목표량
# 판정도 이 주기로 하므로, 짧을수록 오버슈트가 줄어듭니다.
KEEPALIVE_PERIOD_S = 0.05


class DriveNode(Node):
    def __init__(self):
        super().__init__("drive_node")

        # 좌/우 버튼 한 번에 도는 각도. 22.5도면 열여섯 번에 한 바퀴라
        # 방향을 눈으로 가늠하며 조작하기 좋습니다.
        self.declare_parameter("turn_angle_deg", 22.5)
        # 전진/후진 버튼 한 번에 가는 거리.
        self.declare_parameter("step_distance_m", 0.15)
        # 목표량이 안 채워질 때를 대비한 시간 상한. 오도메트리로 예상한
        # 소요 시간의 몇 배까지 기다릴지입니다. 가속 구간이 있어 예상보다
        # 오래 걸리므로 넉넉히 두되, 무한정 돌지는 않게 묶습니다.
        self.declare_parameter("progress_timeout_factor", 3.0)

        self._turn_angle_rad = math.radians(
            self.get_parameter("turn_angle_deg").value
        )
        self._step_distance_m = self.get_parameter("step_distance_m").value
        self._timeout_factor = self.get_parameter(
            "progress_timeout_factor"
        ).value

        self._twist_pub = self.create_publisher(Twist, "cmd_vel_teleop", 10)
        self.create_subscription(
            String, "mission/drive_command", self._on_drive_command, 10
        )
        self.create_subscription(Odometry, "odom", self._on_odom, 10)

        # QoS 1 재전송과 실제 새 명령을 구분하는 데만 씁니다.
        self._last_request_id = None
        self._keepalive_timer = None
        self._active_linear = 0.0
        self._active_angular = 0.0
        # None이면 정지 상태.
        self._deadline = None
        # 오도메트리 최신값 (x, y, yaw). 아직 못 받았으면 None.
        self._pose = None
        # 이번 스텝의 시작 자세와 목표량. 오도메트리가 없으면 None 이고,
        # 그때는 시간만으로 끊습니다(예전 동작).
        self._start_pose = None
        self._goal = 0.0
        self._goal_is_turn = False

        self.get_logger().info("drive_node 시작")

    # --- 콜백 ---

    def _on_odom(self, msg: Odometry):
        q = msg.pose.pose.orientation
        self._pose = (
            msg.pose.pose.position.x,
            msg.pose.pose.position.y,
            yaw_from_quaternion(q.x, q.y, q.z, q.w),
        )

    def _on_drive_command(self, msg: String):
        try:
            command = parse_internal_drive_command(msg.data)
        except CommandError as exc:
            self.get_logger().error(f"주행 내부 명령 거부: {exc}")
            return

        # 같은 requestId가 다시 오면 QoS 1 재전송입니다. 다시 실행하면 버튼
        # 한 번에 두 번 움직이거나 목표량이 두 배가 됩니다.
        if command.request_id == self._last_request_id:
            self.get_logger().info(
                f"중복 주행 명령 무시: requestId={command.request_id}"
            )
            return
        self._last_request_id = command.request_id

        hold_s = hold_seconds(
            command, self._turn_angle_rad, self._step_distance_m
        )
        if hold_s <= 0.0:
            # STOP: 목표량도 시한도 없습니다.
            self._finish("정지 명령")
            self.get_logger().info(
                f"주행 명령 실행: direction={command.direction} (즉시 정지)"
            )
            return

        self._active_linear = command.linear_mps
        self._active_angular = command.angular_rps
        self._goal_is_turn = command.linear_mps == 0.0
        self._goal = (
            self._turn_angle_rad if self._goal_is_turn else self._step_distance_m
        )
        self._start_pose = self._pose
        self._deadline = self.get_clock().now() + Duration(
            seconds=hold_s * self._timeout_factor
        )
        self._publish_twist(self._active_linear, self._active_angular)
        self._restart_keepalive_timer()

        goal_text = (
            f"{math.degrees(self._goal):.1f}도"
            if self._goal_is_turn
            else f"{self._goal:.3f}m"
        )
        source = "오도메트리" if self._start_pose is not None else "시간(오도메트리 없음)"
        self.get_logger().info(
            f"주행 명령 실행: direction={command.direction}, "
            f"linear={command.linear_mps}, angular={command.angular_rps}, "
            f"목표={goal_text}, 판정={source}, 시한={hold_s * self._timeout_factor:.2f}초"
        )

    def _on_keepalive(self):
        if self._deadline is None:
            return

        progress = self._progress()
        if progress is not None and progress >= self._goal:
            self._finish(f"목표 도달 ({self._progress_text(progress)})")
            return

        if self.get_clock().now() >= self._deadline:
            reached = (
                self._progress_text(progress)
                if progress is not None
                else "오도메트리 없음"
            )
            # 목표량을 못 채우고 시한에 걸렸습니다. 바퀴가 헛돌거나 safety 가
            # 막고 있거나 오도메트리가 죽은 것이니 조용히 넘기면 안 됩니다.
            self.get_logger().warn(
                f"목표량을 못 채우고 시한 만료 — 진행 {reached}. "
                f"safety 차단·엔코더·모터 배선을 확인하세요."
            )
            self._finish("시한 만료")
            return

        self._publish_twist(self._active_linear, self._active_angular)

    # --- 진행량 ---

    def _progress(self):
        """이번 스텝의 진행량. 오도메트리가 없으면 None."""
        if self._start_pose is None or self._pose is None:
            return None
        sx, sy, syaw = self._start_pose
        x, y, yaw = self._pose
        if self._goal_is_turn:
            return turn_progress(syaw, yaw)
        return straight_progress(sx, sy, x, y)

    def _progress_text(self, progress):
        if self._goal_is_turn:
            return f"{math.degrees(progress):.1f}도"
        return f"{progress:.3f}m"

    def _finish(self, reason):
        self._deadline = None
        self._start_pose = None
        self._cancel_keepalive_timer()
        self._publish_twist(0.0, 0.0)
        self.get_logger().info(f"주행 스텝 종료: {reason}")

    # --- 타이머 ---

    def _restart_keepalive_timer(self):
        self._destroy_keepalive_timer()
        self._keepalive_timer = self.create_timer(
            KEEPALIVE_PERIOD_S, self._on_keepalive
        )

    def _cancel_keepalive_timer(self):
        # 타이머 콜백 안에서도 불립니다. 실행 중인 타이머를 그 자리에서
        # 파괴하면 rclpy 가 불안정해지므로 여기서는 멈추기만 하고, 실제
        # 파괴는 다음 명령의 _restart 에서 합니다.
        if self._keepalive_timer is not None:
            self._keepalive_timer.cancel()

    def _destroy_keepalive_timer(self):
        # cancel 만 하고 두면 취소된 타이머가 executor 에 계속 쌓입니다.
        if self._keepalive_timer is not None:
            self._keepalive_timer.cancel()
            self.destroy_timer(self._keepalive_timer)
            self._keepalive_timer = None

    # --- 발행 ---

    def _publish_twist(self, linear_mps, angular_rps):
        twist = Twist()
        twist.linear.x = float(linear_mps)
        twist.angular.z = float(angular_rps)
        self._twist_pub.publish(twist)


def main(args=None):
    rclpy.init(args=args)
    node = DriveNode()
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
