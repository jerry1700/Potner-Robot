"""safety — 어떤 노드보다 우선하는 비상 정지 계층.

LiDAR가 폴 위에 달려 있어 스캔 평면 아래는 통째로 사각지대입니다. 문턱,
슬리퍼, 반려동물 밥그릇, 낮은 테이블 다리가 전부 안 보입니다. 그래서
범퍼의 ToF 센서를 LiDAR와 함께 봅니다.

정지 방식은 twist_mux 를 씁니다. 이 노드는 cmd_vel_safety 에 0을 계속
발행하기만 하고, twist_mux 가 최고 우선순위로 이 토픽을 통과시키면서
Nav2와 도킹 노드의 명령을 자동으로 차단합니다. 모터를 직접 건드리거나
전역 플래그를 손으로 관리하지 않는 이유가 이것입니다.
"""

import math

import rclpy
from geometry_msgs.msg import Twist
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import LaserScan, Range
from std_msgs.msg import Bool


class SafetyNode(Node):
    def __init__(self):
        super().__init__("safety")

        self.declare_parameter("scan_stop_distance", 0.18)  # m
        self.declare_parameter("bumper_stop_distance", 0.12)  # m
        self.declare_parameter("front_arc_deg", 60.0)  # 정면 판정 각도 폭
        self.declare_parameter("clear_hysteresis", 0.08)  # m, 깜빡임 방지
        self.declare_parameter("publish_rate", 20.0)

        self._scan_limit = self.get_parameter("scan_stop_distance").value
        self._bumper_limit = self.get_parameter("bumper_stop_distance").value
        self._front_arc = math.radians(self.get_parameter("front_arc_deg").value)
        self._hysteresis = self.get_parameter("clear_hysteresis").value

        self._scan_blocked = False
        self._bumper_blocked = {"left": False, "right": False}

        self.create_subscription(
            LaserScan, "scan", self._on_scan, qos_profile_sensor_data
        )
        self.create_subscription(
            Range, "bumper/left", lambda m: self._on_bumper("left", m),
            qos_profile_sensor_data,
        )
        self.create_subscription(
            Range, "bumper/right", lambda m: self._on_bumper("right", m),
            qos_profile_sensor_data,
        )

        self._stop_pub = self.create_publisher(Twist, "cmd_vel_safety", 10)
        self._state_pub = self.create_publisher(Bool, "safety/blocked", 10)

        period = 1.0 / self.get_parameter("publish_rate").value
        self.create_timer(period, self._tick)

        self.get_logger().info("safety 노드 시작 — LiDAR와 범퍼를 감시합니다.")

    @property
    def blocked(self):
        return self._scan_blocked or any(self._bumper_blocked.values())

    def _on_scan(self, msg: LaserScan):
        """정면 부채꼴 안에서 가장 가까운 유효 거리를 봅니다."""
        nearest = math.inf
        angle = msg.angle_min

        for distance in msg.ranges:
            if -self._front_arc / 2.0 <= angle <= self._front_arc / 2.0:
                # inf, nan, 0 은 측정 실패값이라 버립니다. 이걸 안 거르면
                # 0을 진짜 거리로 읽어서 로봇이 영영 못 움직입니다.
                if msg.range_min < distance < msg.range_max and not math.isnan(distance):
                    nearest = min(nearest, distance)
            angle += msg.angle_increment

        self._scan_blocked = self._apply_hysteresis(
            self._scan_blocked, nearest, self._scan_limit
        )

    def _on_bumper(self, side, msg: Range):
        self._bumper_blocked[side] = self._apply_hysteresis(
            self._bumper_blocked[side], msg.range, self._bumper_limit
        )

    def _apply_hysteresis(self, currently_blocked, distance, limit):
        """경계값에서 켜졌다 꺼졌다 하는 걸 막습니다."""
        if currently_blocked:
            return distance <= limit + self._hysteresis
        return distance <= limit

    def _tick(self):
        state = Bool()
        state.data = self.blocked
        self._state_pub.publish(state)

        if not self.blocked:
            return

        # 0을 계속 발행해야 twist_mux 가 이 입력을 살아있는 것으로 보고
        # 하위 우선순위를 계속 막아줍니다. 한 번만 쏘면 곧 풀립니다.
        self._stop_pub.publish(Twist())
        self.get_logger().warn(
            f"장애물로 정지 중 (scan={self._scan_blocked}, "
            f"bumper={self._bumper_blocked})",
            throttle_duration_sec=2.0,
        )


def main(args=None):
    rclpy.init(args=args)
    node = SafetyNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
