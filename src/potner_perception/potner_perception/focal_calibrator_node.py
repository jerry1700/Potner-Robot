"""focal_calibrator — 마커 하나로 카메라 초점거리를 보정합니다.

체커보드 정식 캘리브레이션(camera_calibration 패키지)은 GUI 창을 띄워야
해서 화면 없는 젯슨에서는 쓸 수 없습니다. 이 도구는 이미 인식되고 있는
ArUco 마커만으로 초점거리를 역산합니다.

원리는 핀홀 모델입니다.

    거리 = 마커실측크기 x 초점거리 / 화면상픽셀크기

거리와 초점거리가 비례하므로, 실제 거리를 알면 초점거리를 바로 구할 수
있습니다.

    올바른 초점거리 = 현재초점거리 x (측정된거리 / 실제거리)

왜곡 계수까지 구하지는 못하지만, 도킹 정확도에는 초점거리가 지배적이라
이 정도로 충분합니다.

사용법:
    터미널 1: ros2 launch potner_bringup robot.launch.py use_lidar:=false
    터미널 2: 마커를 렌즈에서 정확히 50cm 앞에 정면으로 두고
              ros2 run potner_perception focal_calibrator \\
                  --ros-args -p true_distance:=0.5
"""

import statistics

import rclpy
from geometry_msgs.msg import Vector3
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data


class FocalCalibrator(Node):
    def __init__(self):
        super().__init__("focal_calibrator")

        self.declare_parameter("true_distance", 0.5)  # 실제 거리 (m)
        self.declare_parameter("samples", 50)
        self.declare_parameter("current_focal", 600.0)  # 지금 설정된 값
        self.declare_parameter("max_yaw_deg", 15.0)  # 이보다 기울면 버림

        self.true_distance = self.get_parameter("true_distance").value
        self.target_samples = self.get_parameter("samples").value
        self.current_focal = self.get_parameter("current_focal").value
        self.max_yaw = self.get_parameter("max_yaw_deg").value

        self.distances = []
        self.rejected = 0

        self.create_subscription(
            Vector3, "perception/marker_pose", self._on_pose, qos_profile_sensor_data
        )

        self.get_logger().info(
            "마커를 렌즈에서 %.2fm 앞에 정면으로 두세요. %d개 표본을 모읍니다."
            % (self.true_distance, self.target_samples)
        )

    def _on_pose(self, msg: Vector3):
        distance, _lateral, yaw = msg.x, msg.y, msg.z

        # 비스듬히 본 마커는 거리 추정 오차가 큽니다. 정면 것만 씁니다.
        if abs(yaw) > self.max_yaw:
            self.rejected += 1
            return

        self.distances.append(distance)
        count = len(self.distances)

        if count % 10 == 0:
            self.get_logger().info("%d / %d 수집" % (count, self.target_samples))

        if count >= self.target_samples:
            self._report()
            rclpy.shutdown()

    def _report(self):
        # 평균이 아니라 중앙값을 씁니다. 순간적으로 튀는 값 하나가
        # 결과를 통째로 끌고 가는 걸 막습니다.
        measured = statistics.median(self.distances)
        spread = statistics.pstdev(self.distances)
        suggested = self.current_focal * (measured / self.true_distance)
        error_percent = (measured - self.true_distance) / self.true_distance * 100.0

        print("")
        print("=" * 56)
        print("  카메라 초점거리 보정 결과")
        print("=" * 56)
        print("  표본 수           : %d개 (기울어져서 버린 것 %d개)"
              % (len(self.distances), self.rejected))
        print("  측정 거리(중앙값) : %.4f m" % measured)
        print("  표본 흔들림       : %.4f m" % spread)
        print("  실제 거리         : %.4f m" % self.true_distance)
        print("  현재 오차         : %+.1f %%" % error_percent)
        print("-" * 56)
        print("  현재 focal_length_px : %.1f" % self.current_focal)
        print("  권장 focal_length_px : %.1f" % suggested)
        print("=" * 56)
        print("")
        print("  config/potner_params.yaml 의 marker_detector 항목에서")
        print("  focal_length_px 를 %.1f 로 바꾸세요." % suggested)
        print("")

        if spread > 0.02:
            print("  ⚠️  표본이 %.0fmm 나 흔들립니다. 마커나 카메라가 움직였거나"
                  % (spread * 1000))
            print("      조명이 부족합니다. 고정하고 다시 재보세요.")
            print("")


def main(args=None):
    rclpy.init(args=args)
    node = FocalCalibrator()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        if node.distances:
            node._report()
    finally:
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
