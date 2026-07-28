"""ArUco 마커의 거리와 기울기 추정.

legacy/vision/auto_docking_vision.py 의 estimate_distance_and_angle 을 옮긴
순수 계산 모듈입니다. cv2와 numpy만 쓰고 ROS에는 의존하지 않습니다.

거리 단위를 cm에서 **m로 바꿨습니다.** ROS는 SI 단위(m, rad, s)를 쓰는 게
규칙이고, 여기서 cm를 흘리면 Nav2와 TF 전체가 100배 틀어집니다.
"""

from dataclasses import dataclass

import cv2
import numpy as np


@dataclass
class CameraIntrinsics:
    """카메라 내부 파라미터.

    TODO: cv2.calibrateCamera 로 실제 BRIO 100을 캘리브레이션할 것.
    아래는 640x480 기준 추정값이라 거리 오차가 있습니다. 도킹 정밀도가
    안 나오면 여기가 원인일 가능성이 큽니다.
    """

    focal_length_px: float = 600.0
    center_x: float = 320.0
    center_y: float = 240.0

    def matrix(self):
        return np.array(
            [
                [self.focal_length_px, 0.0, self.center_x],
                [0.0, self.focal_length_px, self.center_y],
                [0.0, 0.0, 1.0],
            ],
            dtype=np.float32,
        )

    def distortion(self):
        # TODO: 캘리브레이션 후 실제 왜곡 계수로 교체
        return np.zeros((4, 1), dtype=np.float32)


# 인쇄한 마커의 한 변 길이 (m). create_station_markers 로 뽑은 실물을 자로 재세요.
DEFAULT_MARKER_SIZE = 0.05


def estimate_pose(corners, intrinsics=None, marker_size=DEFAULT_MARKER_SIZE):
    """마커 네 꼭짓점으로 거리(m)와 좌우 기울기(deg)를 계산합니다.

    Args:
        corners: (4, 2) 형태의 픽셀 좌표. cv2.aruco 가 주는 순서 그대로.

    Returns:
        (distance_m, yaw_deg) 또는 추정 실패 시 (None, None)
    """
    intrinsics = intrinsics or CameraIntrinsics()
    half = marker_size / 2.0

    object_points = np.array(
        [
            [-half, half, 0.0],
            [half, half, 0.0],
            [half, -half, 0.0],
            [-half, -half, 0.0],
        ],
        dtype=np.float32,
    )

    image_points = np.asarray(corners, dtype=np.float32).reshape(4, 2)

    success, rvec, tvec = cv2.solvePnP(
        object_points,
        image_points,
        intrinsics.matrix(),
        intrinsics.distortion(),
        flags=cv2.SOLVEPNP_IPPE_SQUARE,
    )

    if not success:
        return None, None

    distance_m = float(tvec[2][0])

    rotation, _ = cv2.Rodrigues(rvec)
    yaw_rad = np.arctan2(rotation[0, 2], rotation[2, 2])
    yaw_deg = float(np.degrees(yaw_rad))

    return distance_m, yaw_deg
