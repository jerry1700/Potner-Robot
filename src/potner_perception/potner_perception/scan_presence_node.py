"""scan_presence — 라이다로 "누가 앞에 왔다"를 알립니다 (귀가 인사용).

`person_detector` 를 고치지 않고 노드를 따로 둔 이유가 둘입니다. 그쪽은
`Image` 소비자라 나중에 ultralytics 를 올려 되살릴 수 있게 남겨두고, 또
**자율 발화 경로가 둘이면 인사 기회를 태울 확률이 두 배**가 되기 때문에
동시에 켜지 않습니다 (`robot.launch.py` 의 `use_person_detector` 기본 false).

발행 계약은 그대로라 `mission_manager` 는 손대지 않았습니다.

    perception/person_present   판정 결과
    perception/pet_present      **항상 False**

반려동물은 원리적으로 못 봅니다 — 스캔 평면이 바닥 52cm 라 고양이·개
위를 지나갑니다. 그래도 계약을 지키려고 발행은 합니다. 이 방식이 포기한
것이고, 무장 시간창이 그 위험을 대신 막습니다.

★ 스캔이 없어도 **매번 발행합니다.** `person_detector` 가 ultralytics 가
  없을 때 아무것도 발행하지 않아서, "사람이 없다"인지 "노드가 죽었다"인지
  구분할 수 없던 적이 있습니다. 조용한 것은 언제나 고장이어야 합니다.
"""

import math

import rclpy
from geometry_msgs.msg import Twist
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import LaserScan
from std_msgs.msg import Bool

from potner_perception.scan_presence import (
    PresenceConfig,
    ScanFrame,
    ScanPresenceDetector,
)


class ScanPresence(Node):
    def __init__(self):
        super().__init__("scan_presence")

        self.declare_parameter("sector_deg", 100.0)
        self.declare_parameter("min_range_m", 0.40)
        self.declare_parameter("max_range_m", 2.50)
        self.declare_parameter("baseline_margin_m", 0.12)
        self.declare_parameter("leg_width_m", 0.12)
        self.declare_parameter("width_tolerance", 0.5)
        self.declare_parameter("persist_scans", 3)
        self.declare_parameter("baseline_scans", 10)
        self.declare_parameter("settle_seconds", 1.5)
        self.declare_parameter("motion_epsilon", 0.02)

        config = PresenceConfig(
            sector_deg=self.get_parameter("sector_deg").value,
            min_range_m=self.get_parameter("min_range_m").value,
            max_range_m=self.get_parameter("max_range_m").value,
            baseline_margin_m=self.get_parameter("baseline_margin_m").value,
            leg_width_m=self.get_parameter("leg_width_m").value,
            width_tolerance=self.get_parameter("width_tolerance").value,
            persist_scans=self.get_parameter("persist_scans").value,
            baseline_scans=self.get_parameter("baseline_scans").value,
            settle_seconds=self.get_parameter("settle_seconds").value,
            motion_epsilon=self.get_parameter("motion_epsilon").value,
        )
        self._detector = ScanPresenceDetector(config)
        self._present = False
        self._warned_self_occlusion = False

        # ★ sensor_data QoS 여야 합니다. 드라이버가 BEST_EFFORT 로 내는데
        #   기본값(RELIABLE)으로 구독하면 QoS 비호환으로 연결 자체가 성립하지
        #   않습니다 — 예외도 경고도 없이 콜백이 한 번도 안 불리고 노드는
        #   멀쩡해 보입니다. safety_node 가 쓰는 것과 같게 맞춥니다.
        self.create_subscription(
            LaserScan, "scan", self._on_scan, qos_profile_sensor_data
        )
        # 주행 중에는 배경이 무의미합니다. 발행은 하지 않고 읽기만 합니다.
        self.create_subscription(Twist, "cmd_vel", self._on_cmd_vel, 10)

        self._person_pub = self.create_publisher(Bool, "perception/person_present", 10)
        self._pet_pub = self.create_publisher(Bool, "perception/pet_present", 10)

        self.get_logger().info(
            f"scan_presence 시작 — 정면 {config.sector_deg:.0f}도, "
            f"{config.min_range_m:.2f}~{config.max_range_m:.2f}m 감시"
        )

    # --- 콜백 ---

    def _on_cmd_vel(self, msg: Twist):
        # 회전만 해도 배경이 통째로 바뀌므로 각속도도 함께 봅니다.
        speed = math.hypot(msg.linear.x, msg.angular.z * 0.1)
        self._detector.note_motion(self._now(), speed)

    def _on_scan(self, msg: LaserScan):
        frame = ScanFrame(
            ranges=list(msg.ranges),
            angle_min=msg.angle_min,
            angle_increment=msg.angle_increment,
            range_min=msg.range_min,
            range_max=msg.range_max,
        )

        was_ready = self._detector.ready
        present = self._detector.feed(self._now(), frame)
        if self._detector.ready and not was_ready:
            self._on_baseline_ready()

        if present != self._present:
            self._present = present
            self.get_logger().info(
                "사람 감지" if present else "사람 사라짐"
            )

        self._person_pub.publish(Bool(data=present))
        # 라이다로는 볼 수 없습니다 (스캔 평면이 반려동물 위).
        self._pet_pub.publish(Bool(data=False))

    # --- 진단 ---

    def _on_baseline_ready(self):
        """배경을 다 잡은 순간, 그 배경 자체가 이상한지 봅니다.

        자기 몸(폴 위 화분 잎, 얼굴 화면)이 스캔 평면에 들어와 있으면
        배경에 아주 가까운 값이 상시 찍힙니다. 그 상태는 감지 문제가 아니라
        **safety 가 영구히 막혀 로봇이 못 움직이는** 물리 문제라서, 조용히
        넘기면 원인을 찾는 데 며칠이 걸립니다. ydlidar.yaml 의
        ``ignore_array`` 가 비어 있어 자기 가림 마스킹이 하나도 없습니다.

        기준선은 ``min_range_m`` 을 그대로 씁니다. safety 의 해제선을 여기에
        따로 적으면 그쪽 값이 바뀔 때 같이 안 바뀌어 낡습니다 — 실제로
        ``scan_stop_distance`` 가 0.25 에서 0.18 로 바뀐 적이 있습니다.
        ``min_range_m`` 이 해제선 바깥이라는 것은
        ``tests/test_config_consistency.py`` 가 yaml 을 읽어 강제합니다.
        """
        limit = self._detector.config.min_range_m
        close = [
            value
            for value in self._detector.baseline
            if value is not None and value < limit
        ]
        self.get_logger().info(f"배경 수집 완료 (빔 {len(self._detector.baseline)}개)")

        if close and not self._warned_self_occlusion:
            self._warned_self_occlusion = True
            self.get_logger().warn(
                f"배경에 {limit:.2f}m 안쪽 값이 {len(close)}개 있습니다 "
                f"(가장 가까운 것 {min(close):.2f}m). 로봇 자신의 부품이 스캔 "
                "평면에 들어와 있으면 safety 가 영구히 막힙니다 — "
                "ros2 topic echo /safety/blocked 로 확인하세요."
            )

    def _now(self):
        return self.get_clock().now().nanoseconds * 1e-9


def main(args=None):
    rclpy.init(args=args)
    node = ScanPresence()
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
