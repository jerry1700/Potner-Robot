"""pose_report — 지금 로봇이 서 있는 좌표를 읽기 좋은 형태로 내보냅니다.

좌표 등록에 쓰는 도구입니다. 로봇을 등록할 자리로 데려간 뒤:

    ros2 topic echo /robot/pose --once

에 나온 JSON 을 서버의 ``PUT /locations/{type}/pose`` 에 그대로 붙이면
됩니다. 앱이 좌표를 자동으로 채워 주더라도, 값이 맞는지 눈으로 대조할
때 이 값을 씁니다.

★ 이 좌표는 **오도메트리 원점 기준**입니다. 로봇을 재시작하면 원점이 그
  자리로 새로 잡혀 **이전에 등록한 좌표가 전부 무의미해집니다.** 바닥에
  원점을 표시해 두고 늘 같은 자리·같은 방향에서 띄우세요.

★ 서버로 직접 보내지 않습니다. 브로커 ACL 이 계약에 없는 토픽의 발행을
  조용히 버리기 때문에, 서버 팀과 토픽을 합의하기 전에 보내면 아무 데도
  도착하지 않으면서 성공한 것처럼 보입니다 (DEVICE-MQTT.md).
"""

import rclpy
from nav_msgs.msg import Odometry
from rclpy.node import Node
from std_msgs.msg import String

from potner_mission.pose_report import (
    pose_json,
    pose_summary,
    yaw_from_quaternion,
)


class PoseReport(Node):
    def __init__(self):
        super().__init__("pose_report")

        self.declare_parameter("publish_period", 1.0)
        # 화면으로도 찍을지. 기본으로 끕니다 — 켜면 다른 노드의 로그가
        # 묻혀서, 문제가 생겼을 때 원인을 찾기 어려워집니다.
        self.declare_parameter("log_pose", False)

        self._pose = None
        self._pub = self.create_publisher(String, "robot/pose", 10)
        self.create_subscription(Odometry, "odom", self._on_odom, 10)

        period = self.get_parameter("publish_period").value
        self.create_timer(period, self._tick)

        self.get_logger().info(
            "pose_report 시작 — 좌표 등록은 "
            "ros2 topic echo /robot/pose --once 로 읽으세요."
        )

    def _on_odom(self, msg: Odometry):
        q = msg.pose.pose.orientation
        self._pose = (
            msg.pose.pose.position.x,
            msg.pose.pose.position.y,
            yaw_from_quaternion(q.x, q.y, q.z, q.w),
        )

    def _tick(self):
        if self._pose is None:
            return
        x, y, yaw = self._pose
        self._pub.publish(String(data=pose_json(x, y, yaw)))
        if self.get_parameter("log_pose").value:
            self.get_logger().info(pose_summary(x, y, yaw))


def main(args=None):
    rclpy.init(args=args)
    node = PoseReport()
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
