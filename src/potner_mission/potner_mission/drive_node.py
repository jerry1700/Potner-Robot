"""drive_node — 휴대폰 방향 버튼(수동 주행)을 실제 움직임으로 바꾸는 곳.

mission_manager 의 자율주행 상태머신(IDLE/NAVIGATING/DOCKING/...)과는 무관한
별도 관심사라 노드를 분리했습니다. 서버가 속도·지속시간을 전부 정해서
보내고, 로봇은 그 값을 그대로 twist_mux 의 teleop 슬롯(우선순위 200 —
Nav2 100보다 높고 safety 255보다 낮음, config/twist_mux.yaml)으로 흘려보낼
뿐입니다.

durationMs 가 지나면 스스로 0 Twist 를 발행해 멈춥니다. base_driver 의
cmd_vel_timeout(기본 0.5초) 안전장치에만 기대면, durationMs(기본 600ms)가
그보다 길 때 버튼 한 번에 로봇이 멈췄다 다시 움직이는 것처럼 보입니다.

drive는 회신(``result/drive``) 계약이 없습니다 — 서버의
``CommandResultTopicParser`` 가 water/capture/fan/navigate 만 인식해서,
보내도 조용히 버려집니다.
"""

import rclpy
from geometry_msgs.msg import Twist
from rclpy.node import Node
from std_msgs.msg import String

from potner_bridge.command_result import CommandError
from potner_bridge.drive_contract import parse_internal_drive_command


class DriveNode(Node):
    def __init__(self):
        super().__init__("drive_node")

        self._twist_pub = self.create_publisher(Twist, "cmd_vel_teleop", 10)
        self.create_subscription(
            String, "mission/drive_command", self._on_drive_command, 10
        )

        # QoS 1 재전송과 실제 새 명령을 구분하는 데만 씁니다.
        self._last_request_id = None
        self._stop_timer = None

        self.get_logger().info("drive_node 시작")

    def _on_drive_command(self, msg: String):
        try:
            command = parse_internal_drive_command(msg.data)
        except CommandError as exc:
            self.get_logger().error(f"주행 내부 명령 거부: {exc}")
            return

        # 같은 requestId가 다시 오면 QoS 1 재전송입니다. 다시 실행하면
        # 버튼 한 번에 두 번 움직이거나, 이미 정해둔 정지 타이머가 늘어나
        # durationMs 계약이 깨집니다.
        if command.request_id == self._last_request_id:
            self.get_logger().info(
                f"중복 주행 명령 무시: requestId={command.request_id}"
            )
            return
        self._last_request_id = command.request_id

        self._cancel_stop_timer()
        self._publish_twist(command.linear_mps, command.angular_rps)

        if command.duration_ms > 0:
            self._stop_timer = self.create_timer(
                command.duration_ms / 1000.0, self._on_duration_elapsed
            )
        self.get_logger().info(
            f"주행 명령 실행: direction={command.direction}, "
            f"linear={command.linear_mps}, angular={command.angular_rps}, "
            f"durationMs={command.duration_ms}"
        )

    def _on_duration_elapsed(self):
        # rclpy 타이머는 반복 실행이라, 콜백 맨 처음에 스스로 취소해야
        # 한 번만 동작한 것처럼 씁니다(mission_manager의 다른 타이머들과
        # 같은 방식).
        self._cancel_stop_timer()
        self._publish_twist(0.0, 0.0)

    def _cancel_stop_timer(self):
        if self._stop_timer is not None:
            self._stop_timer.cancel()
            self._stop_timer = None

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
