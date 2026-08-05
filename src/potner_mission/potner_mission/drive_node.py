"""drive_node — 휴대폰 방향 버튼(수동 주행)을 실제 움직임으로 바꾸는 곳.

mission_manager 의 자율주행 상태머신(IDLE/NAVIGATING/DOCKING/...)과는 무관한
별도 관심사라 노드를 분리했습니다. 서버가 속도·지속시간을 전부 정해서
보내고, 로봇은 그 값을 그대로 twist_mux 의 teleop 슬롯(우선순위 200 —
Nav2 100보다 높고 safety 255보다 낮음, config/twist_mux.yaml)으로 흘려보낼
뿐입니다.

durationMs 가 지나면 스스로 0 Twist 를 발행해 멈춥니다. base_driver 의
cmd_vel_timeout(기본 0.5초) 안전장치에만 기대면, durationMs(기본 600ms)가
그보다 길 때 버튼 한 번에 로봇이 멈췄다 다시 움직이는 것처럼 보입니다.

★ durationMs 동안 딱 한 번만 발행하고 가만히 있으면 안 됩니다. twist_mux
  의 teleop 타임아웃(0.5초, config/twist_mux.yaml)이 durationMs(기본
  600ms)보다 짧아서, twist_mux 가 먼저 "이 입력이 죽었다"고 보고 끊어
  버립니다. 그래서 타임아웃보다 확실히 짧은 주기(0.2초)로 같은 값을
  계속 재발행합니다.

drive는 회신(``result/drive``) 계약이 없습니다 — 서버의
``CommandResultTopicParser`` 가 water/capture/fan/navigate 만 인식해서,
보내도 조용히 버려집니다.
"""

import rclpy
from geometry_msgs.msg import Twist
from rclpy.duration import Duration
from rclpy.node import Node
from std_msgs.msg import String

from potner_bridge.command_result import CommandError
from potner_bridge.drive_contract import parse_internal_drive_command

# twist_mux 의 teleop 타임아웃(0.5초)보다 확실히 짧아야, durationMs 동안
# twist_mux 가 이 입력을 죽은 것으로 보고 먼저 끊는 일이 없습니다.
KEEPALIVE_PERIOD_S = 0.2


class DriveNode(Node):
    def __init__(self):
        super().__init__("drive_node")

        self._twist_pub = self.create_publisher(Twist, "cmd_vel_teleop", 10)
        self.create_subscription(
            String, "mission/drive_command", self._on_drive_command, 10
        )

        # QoS 1 재전송과 실제 새 명령을 구분하는 데만 씁니다.
        self._last_request_id = None
        self._keepalive_timer = None
        self._active_linear = 0.0
        self._active_angular = 0.0
        # None이면 정지 상태. 값이 있으면 그 시각까지 재발행을 계속합니다.
        self._stop_at = None

        self.get_logger().info("drive_node 시작")

    def _on_drive_command(self, msg: String):
        try:
            command = parse_internal_drive_command(msg.data)
        except CommandError as exc:
            self.get_logger().error(f"주행 내부 명령 거부: {exc}")
            return

        # 같은 requestId가 다시 오면 QoS 1 재전송입니다. 다시 실행하면
        # 버튼 한 번에 두 번 움직이거나, 이미 정해둔 정지 시각이 늘어나
        # durationMs 계약이 깨집니다.
        if command.request_id == self._last_request_id:
            self.get_logger().info(
                f"중복 주행 명령 무시: requestId={command.request_id}"
            )
            return
        self._last_request_id = command.request_id

        self._active_linear = command.linear_mps
        self._active_angular = command.angular_rps
        self._publish_twist(self._active_linear, self._active_angular)

        if command.duration_ms > 0:
            self._stop_at = self.get_clock().now() + Duration(
                seconds=command.duration_ms / 1000.0
            )
            self._ensure_keepalive_timer()
        else:
            # STOP: 재발행할 것도 없으니 바로 끝냅니다.
            self._stop_at = None
            self._cancel_keepalive_timer()

        self.get_logger().info(
            f"주행 명령 실행: direction={command.direction}, "
            f"linear={command.linear_mps}, angular={command.angular_rps}, "
            f"durationMs={command.duration_ms}"
        )

    def _ensure_keepalive_timer(self):
        # rclpy 타이머는 반복 실행이라, 이미 돌고 있으면 새로 만들지
        # 않습니다 — 겹쳐서 여러 개 도는 걸 막습니다.
        if self._keepalive_timer is None:
            self._keepalive_timer = self.create_timer(
                KEEPALIVE_PERIOD_S, self._on_keepalive
            )

    def _cancel_keepalive_timer(self):
        if self._keepalive_timer is not None:
            self._keepalive_timer.cancel()
            self._keepalive_timer = None

    def _on_keepalive(self):
        if self._stop_at is None:
            return

        if self.get_clock().now() >= self._stop_at:
            self._stop_at = None
            self._cancel_keepalive_timer()
            self._publish_twist(0.0, 0.0)
            return

        self._publish_twist(self._active_linear, self._active_angular)

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
