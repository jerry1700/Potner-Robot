"""docking_server — Nav2가 스테이션 앞까지 데려다 준 뒤 최종 접근을 맡습니다.

Nav2는 지도 좌표까지 데려다주는 데는 뛰어나지만 도착 오차가 수십 cm
수준입니다. 충전 단자를 맞추려면 cm 단위가 필요해서 마지막 구간만
마커를 보며 따로 제어합니다.

주의 — 스테이션 초음파(HC-SR04)를 제어 루프에 넣지 마세요. 그 값은
스테이션 ESP32 -> 인터넷 -> EC2 브로커 -> 인터넷 -> 로봇 경로로 오기
때문에 왕복 지연이 수백 ms에 지터까지 있습니다. 이 노드는 로봇에 달린
카메라만 보고 제어하고, 스테이션 신호는 완료 판정에만 씁니다.
"""

import rclpy
from geometry_msgs.msg import Twist, Vector3
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from std_msgs.msg import Bool, Int32

from potner_docking.approach_controller import DockingGains, compute

# TODO: potner_msgs 빌드 후 액션 서버로 전환
# from rclpy.action import ActionServer
# from potner_msgs.action import DockToStation


class DockingServer(Node):
    def __init__(self):
        super().__init__("docking_server")

        self.declare_parameter("target_marker_id", -1)  # -1이면 대기
        self.declare_parameter("kp_lateral", 0.0025)
        self.declare_parameter("kp_yaw", 0.017)
        self.declare_parameter("approach_speed", 0.12)
        self.declare_parameter("max_angular", 0.8)
        self.declare_parameter("target_distance", 0.15)
        self.declare_parameter("marker_lost_timeout", 2.0)
        self.declare_parameter("docking_timeout", 90.0)

        self.gains = DockingGains(
            kp_lateral=self.get_parameter("kp_lateral").value,
            kp_yaw=self.get_parameter("kp_yaw").value,
            approach_speed=self.get_parameter("approach_speed").value,
            max_angular=self.get_parameter("max_angular").value,
            target_distance=self.get_parameter("target_distance").value,
        )

        self._target_id = self.get_parameter("target_marker_id").value
        self._visible_id = -1
        self._pose = None
        self._last_seen = None
        self._started_at = None
        self._station_confirms = False

        self.create_subscription(
            Vector3, "perception/marker_pose", self._on_pose, qos_profile_sensor_data
        )
        self.create_subscription(
            Int32, "perception/marker_id", self._on_id, qos_profile_sensor_data
        )
        # 스테이션 ESP32가 MQTT로 올려보낸 홀 센서 접점 (mqtt_bridge 가 중계)
        self.create_subscription(Bool, "station/docked", self._on_station, 10)

        self._cmd_pub = self.create_publisher(Twist, "cmd_vel_docking", 10)
        self._done_pub = self.create_publisher(Bool, "docking/complete", 10)

        self.create_timer(0.05, self._tick)  # 20Hz

    def _on_id(self, msg: Int32):
        self._visible_id = msg.data
        if msg.data == self._target_id:
            self._last_seen = self.get_clock().now()

    def _on_pose(self, msg: Vector3):
        self._pose = (msg.x, msg.y, msg.z)  # 거리(m), 좌우오차(px), 기울기(deg)

    def _on_station(self, msg: Bool):
        """스테이션의 A3144 홀 센서가 로봇 자석을 감지했다는 신호.

        이산 신호라 네트워크 지연이 문제되지 않고, 카메라보다 확실한
        물리적 접촉 증거입니다. 최종 완료 판정은 이 값을 우선합니다.
        """
        self._station_confirms = msg.data

    def _tick(self):
        if self._target_id < 0:
            return  # 도킹 임무가 없는 상태

        if self._started_at is None:
            self._started_at = self.get_clock().now()

        if self._timed_out():
            self.get_logger().error("도킹 시간 초과 — 중단합니다.")
            self._finish(success=False)
            return

        if self._station_confirms:
            self.get_logger().info("스테이션 홀 센서 접점 확인 — 도킹 완료")
            self._finish(success=True)
            return

        if not self._marker_fresh():
            # 마커를 놓쳤으면 멈춥니다. legacy 코드처럼 계속 전진하면
            # 스테이션을 들이받습니다.
            self._cmd_pub.publish(Twist())
            self.get_logger().warn("마커를 놓쳤습니다.", throttle_duration_sec=1.0)
            return

        distance_m, lateral_px, yaw_deg = self._pose
        command = compute(distance_m, lateral_px, yaw_deg, self.gains)

        twist = Twist()
        twist.linear.x = command.linear
        twist.angular.z = command.angular
        self._cmd_pub.publish(twist)

        if command.docked:
            self.get_logger().info("비전 기준 정렬 완료 — 스테이션 확인 대기")

    def _marker_fresh(self):
        if self._pose is None or self._last_seen is None:
            return False
        timeout = self.get_parameter("marker_lost_timeout").value
        age = (self.get_clock().now() - self._last_seen).nanoseconds * 1e-9
        return age < timeout

    def _timed_out(self):
        limit = self.get_parameter("docking_timeout").value
        elapsed = (self.get_clock().now() - self._started_at).nanoseconds * 1e-9
        return elapsed > limit

    def _finish(self, success):
        self._cmd_pub.publish(Twist())
        self._done_pub.publish(Bool(data=success))
        self._target_id = -1
        self._started_at = None
        self._station_confirms = False


def main(args=None):
    rclpy.init(args=args)
    node = DockingServer()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
