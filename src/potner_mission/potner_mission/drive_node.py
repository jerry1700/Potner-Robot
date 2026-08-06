"""drive_node — 휴대폰 방향 버튼(수동 주행)을 실제 움직임으로 바꾸는 곳.

mission_manager 의 자율주행 상태머신(IDLE/NAVIGATING/DOCKING/...)과는 무관한
별도 관심사라 노드를 분리했습니다. 서버가 속도·지속시간을 전부 정해서
보내고, 로봇은 그 값을 그대로 twist_mux 의 teleop 슬롯(우선순위 200 —
Nav2 100보다 높고 safety 255보다 낮음, config/twist_mux.yaml)으로 흘려보낼
뿐입니다.

버튼 한 번이 늘 같은 양을 움직입니다 — **전진·후진은 step_distance_m
(기본 0.15m), 좌/우 회전은 turn_angle_deg(기본 22.5도)**. 서버가 준
durationMs 를 쓰지 않는 이유는, 그대로 쓰면 가속에만 시간을 다 쓰고
실측 2~3cm 밖에 못 가서 좌표 등록용으로 쓸 수 없었기 때문입니다.
22.5도면 열여섯 번에 한 바퀴라 방향을 눈으로 가늠하기 좋습니다.

base_driver 의 cmd_vel_timeout(기본 0.5초) 안전장치에만 기대면, 유지
시간이 그보다 길 때 버튼 한 번에 로봇이 멈췄다 다시 움직이는 것처럼
보입니다. 그래서 자체 정지를 따로 둡니다.

★ durationMs 동안 딱 한 번만 발행하고 가만히 있으면 안 됩니다. twist_mux
  의 teleop 타임아웃(0.5초, config/twist_mux.yaml)이 durationMs(기본
  600ms)보다 짧아서, twist_mux 가 먼저 "이 입력이 죽었다"고 보고 끊어
  버립니다. 그래서 타임아웃보다 확실히 짧은 주기(0.2초)로 같은 값을
  계속 재발행합니다.

drive는 회신(``result/drive``) 계약이 없습니다 — 서버의
``CommandResultTopicParser`` 가 water/capture/fan/navigate 만 인식해서,
보내도 조용히 버려집니다.
"""

import math

import rclpy
from geometry_msgs.msg import Twist
from rclpy.duration import Duration
from rclpy.node import Node
from std_msgs.msg import String

from potner_bridge.command_result import CommandError
from potner_bridge.drive_contract import hold_seconds, parse_internal_drive_command

# 재발행 주기의 상한. twist_mux 의 teleop 타임아웃(0.5초)보다 확실히 짧아야
# 유지 중에 twist_mux 가 이 입력을 죽은 것으로 보고 먼저 끊지 않습니다.
# 실제 주기는 유지 시간을 정수 등분해서 정하므로 이 값보다 짧아집니다.
MAX_KEEPALIVE_PERIOD_S = 0.1


class DriveNode(Node):
    def __init__(self):
        super().__init__("drive_node")

        # 좌/우 버튼 한 번에 도는 각도. 22.5도면 열여섯 번에 한 바퀴라
        # 방향을 눈으로 가늠하며 조작하기 좋습니다.
        self.declare_parameter("turn_angle_deg", 22.5)
        # 전진/후진 버튼 한 번에 가는 거리. 서버 기본값(600ms)대로 두면 실측
        # 2~3cm 밖에 못 가서 좌표 등록용으로 쓸 수 없었습니다.
        self.declare_parameter("step_distance_m", 0.15)
        self._turn_angle_rad = math.radians(
            self.get_parameter("turn_angle_deg").value
        )
        self._step_distance_m = self.get_parameter("step_distance_m").value

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

        hold_s = hold_seconds(
            command, self._turn_angle_rad, self._step_distance_m
        )
        if hold_s > 0.0:
            self._stop_at = self.get_clock().now() + Duration(seconds=hold_s)
            self._restart_keepalive_timer(hold_s)
        else:
            # STOP: 재발행할 것도 없으니 바로 끝냅니다.
            self._stop_at = None
            self._cancel_keepalive_timer()

        self.get_logger().info(
            f"주행 명령 실행: direction={command.direction}, "
            f"linear={command.linear_mps}, angular={command.angular_rps}, "
            f"유지={hold_s:.3f}초"
        )

    def _restart_keepalive_timer(self, hold_s):
        """유지 시간을 정수 등분해 마지막 틱이 정확히 끝에 떨어지게 합니다.

        정지 판정이 이 틱에서만 일어나므로 **주기가 곧 유지 시간 오차**입니다.
        주기를 0.2초로 고정해 두면 0.654초 회전 명령이 실제로는 0.8초 유지돼
        각도가 22%(22.5도 -> 27.5도) 커집니다. 유지 시간의 약수로 잡으면 그
        오차가 사라집니다.
        """
        self._destroy_keepalive_timer()
        ticks = max(1, math.ceil(hold_s / MAX_KEEPALIVE_PERIOD_S))
        self._keepalive_timer = self.create_timer(
            hold_s / ticks, self._on_keepalive
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
