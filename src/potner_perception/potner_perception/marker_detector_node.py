"""marker_detector — 화면에서 ArUco 스테이션 마커를 찾아 위치를 발행합니다.

legacy/vision/auto_docking_vision.py 의 마커 탐지 부분을 옮겼습니다.
계산 자체는 potner_perception.marker_pose 에 순수 함수로 분리해서,
카메라 없이도 단위 테스트할 수 있게 했습니다.

발행하는 값은 거리(m), 화면 좌우 오차(px), 기울기(deg) 세 가지이고,
이걸 potner_docking 의 접근 컨트롤러가 구독합니다.
"""

import rclpy
from geometry_msgs.msg import Vector3
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import Image
from std_msgs.msg import Int32

from potner_perception.marker_pose import (
    DEFAULT_FOCAL_LENGTH_PX,
    DEFAULT_MARKER_SIZE,
    CameraIntrinsics,
    create_detector,
    estimate_pose,
)

try:
    import cv2
    from cv_bridge import CvBridge
except ImportError:
    cv2 = None
    CvBridge = None


class MarkerDetector(Node):
    def __init__(self):
        super().__init__("marker_detector")

        self.declare_parameter("marker_size", DEFAULT_MARKER_SIZE)
        self.declare_parameter("focal_length_px", DEFAULT_FOCAL_LENGTH_PX)
        self.declare_parameter("image_width", 640)
        self.declare_parameter("image_height", 480)

        width = self.get_parameter("image_width").value
        height = self.get_parameter("image_height").value
        self._image_width = width
        self._marker_size = self.get_parameter("marker_size").value
        self._intrinsics = CameraIntrinsics(
            focal_length_px=self.get_parameter("focal_length_px").value,
            center_x=width / 2.0,
            center_y=height / 2.0,
        )

        self._bridge = CvBridge() if CvBridge else None
        self._detector = self._build_detector()

        self.create_subscription(
            Image, "image_raw", self._on_image, qos_profile_sensor_data
        )
        # x=거리(m), y=좌우 오차(px), z=기울기(deg)
        self._pose_pub = self.create_publisher(Vector3, "perception/marker_pose", 10)
        self._id_pub = self.create_publisher(Int32, "perception/marker_id", 10)

    def _build_detector(self):
        if cv2 is None:
            self.get_logger().warn("OpenCV 미설치 — 탐지를 건너뜁니다.")
            return None
        # OpenCV 4.5(젯팩 6 시스템)와 4.7 이상(개발 PC) 양쪽을 지원합니다.
        self.get_logger().info(f"OpenCV {cv2.__version__} 로 ArUco 탐지기 생성")
        return create_detector()

    def _on_image(self, msg: Image):
        if self._detector is None:
            return

        frame = self._bridge.imgmsg_to_cv2(msg, "bgr8")
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        corners, ids, _ = self._detector(gray)

        if ids is None or len(ids) == 0:
            self._id_pub.publish(Int32(data=-1))
            return

        # 여러 개가 보이면 가장 가까운(= 화면에서 가장 큰) 마커를 씁니다.
        best_index = max(range(len(ids)), key=lambda i: _corner_area(corners[i][0]))
        marker_id = int(ids.flatten()[best_index])
        marker_corners = corners[best_index][0]

        distance_m, yaw_deg = estimate_pose(
            marker_corners, self._intrinsics, self._marker_size
        )
        if distance_m is None:
            self._id_pub.publish(Int32(data=-1))
            return

        center_x = sum(corner[0] for corner in marker_corners) / 4.0
        lateral_error_px = center_x - (self._image_width / 2.0)

        self._id_pub.publish(Int32(data=marker_id))
        self._pose_pub.publish(
            Vector3(x=float(distance_m), y=float(lateral_error_px), z=float(yaw_deg))
        )


def _corner_area(corners):
    xs = [c[0] for c in corners]
    ys = [c[1] for c in corners]
    return (max(xs) - min(xs)) * (max(ys) - min(ys))


def main(args=None):
    rclpy.init(args=args)
    node = MarkerDetector()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
