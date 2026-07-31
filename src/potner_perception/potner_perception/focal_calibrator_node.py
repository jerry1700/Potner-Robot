"""focal_calibrator — 마커 하나로 카메라 초점거리를 보정합니다.

체커보드 정식 캘리브레이션(camera_calibration 패키지)은 GUI 창을 띄워야
해서 화면 없는 젯슨에서는 쓸 수 없습니다. 이 도구는 이미 인식되고 있는
ArUco 마커만으로 초점거리를 역산합니다.

핀홀 모델에서 거리와 초점거리는 비례합니다.

    거리 = 마커실측크기 x 초점거리 / 화면상픽셀크기
    올바른 초점거리 = 현재초점거리 x (측정된거리 / 실제거리)

왜곡 계수는 구하지 못하지만 도킹 정확도는 초점거리가 지배적이라 이
정도로 충분합니다.

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

from potner_perception.calibration import (
    distance_error_percent,
    horizontal_fov_deg,
    suggest_focal_length,
)

# 표본이 이보다 흔들리면 마커나 카메라가 고정되지 않았다고 봅니다 (m).
SPREAD_WARNING = 0.02


class FocalCalibrator(Node):
    def __init__(self):
        super().__init__("focal_calibrator")

        self.declare_parameter("true_distance", 0.5)
        self.declare_parameter("samples", 50)
        self.declare_parameter("current_focal", 876.4)
        self.declare_parameter("max_yaw_deg", 15.0)
        self.declare_parameter("image_width", 640)

        self.true_distance = self.get_parameter("true_distance").value
        self.target_samples = self.get_parameter("samples").value
        self.current_focal = self.get_parameter("current_focal").value
        self.max_yaw = self.get_parameter("max_yaw_deg").value
        self.image_width = self.get_parameter("image_width").value

        self.distances = []
        self.rejected = 0

        self.create_subscription(
            Vector3, "perception/marker_pose", self._on_pose, qos_profile_sensor_data
        )

        self.get_logger().info(
            f"마커를 렌즈에서 {self.true_distance:.2f}m 앞에 정면으로 두세요. "
            f"{self.target_samples}개 표본을 모읍니다."
        )

    def _on_pose(self, msg: Vector3):
        distance, _lateral, yaw = msg.x, msg.y, msg.z

        # 비스듬히 본 마커는 거리 오차가 큽니다. 정면 것만 씁니다.
        if abs(yaw) > self.max_yaw:
            self.rejected += 1
            return

        self.distances.append(distance)
        count = len(self.distances)

        if count % 10 == 0:
            self.get_logger().info(f"{count} / {self.target_samples} 수집")

        if count >= self.target_samples:
            self.report()
            rclpy.shutdown()

    def report(self):
        """보정 결과를 출력합니다.

        로거가 아니라 print 를 쓰는 이유는, 숫자를 읽어 설정에 옮겨 적는
        용도라 타임스탬프 접두사가 방해되기 때문입니다.
        """
        # 평균이 아니라 중앙값을 씁니다. 순간적으로 튀는 값 하나가 결과를
        # 통째로 끌고 가는 걸 막습니다.
        measured = statistics.median(self.distances)
        spread = statistics.pstdev(self.distances)
        suggested = suggest_focal_length(
            self.current_focal, measured, self.true_distance
        )
        error = distance_error_percent(measured, self.true_distance)

        print("")
        print("카메라 초점거리 보정 결과")
        print(f"  표본 수            {len(self.distances)}개 "
              f"(기울어져서 버림 {self.rejected}개)")
        print(f"  측정 거리(중앙값)  {measured:.4f} m")
        print(f"  표본 흔들림        {spread:.4f} m")
        print(f"  실제 거리          {self.true_distance:.4f} m")
        print(f"  현재 오차          {error:+.1f} %")
        print("")
        print(f"  focal_length_px    {self.current_focal:.1f} -> {suggested:.1f}")
        print(f"  수평 화각          "
              f"{horizontal_fov_deg(suggested, self.image_width):.1f}도")
        print("")
        print("  config/potner_params.yaml 의 marker_detector 항목에 반영하세요.")
        print("  수평 화각이 카메라 사양과 크게 다르면 마커 크기를 다시 재세요.")

        if spread > SPREAD_WARNING:
            print("")
            print(f"  경고: 표본이 {spread * 1000:.0f}mm 흔들립니다. 마커나 카메라가")
            print("        움직였거나 조명이 부족합니다. 고정하고 다시 재세요.")
        print("")


def main(args=None):
    rclpy.init(args=args)
    node = FocalCalibrator()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        # 표본이 다 모이기 전에 끊어도 지금까지 모인 것으로 계산해 줍니다.
        if node.distances:
            node.report()
    finally:
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
