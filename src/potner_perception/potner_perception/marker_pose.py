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

# beacon 이미지를 뽑을 때 쓴 사전. 바꾸면 기존 마커를 못 읽습니다.
DEFAULT_DICTIONARY = cv2.aruco.DICT_6X6_250


def create_detector(dictionary_id=DEFAULT_DICTIONARY):
    """OpenCV 버전에 상관없이 동작하는 마커 탐지 함수를 돌려줍니다.

    ArUco 파이썬 API는 OpenCV 4.7 에서 한 번 갈아엎어졌습니다.

        4.7 이상   getPredefinedDictionary + ArucoDetector 객체
        4.5 이하   Dictionary_get + detectMarkers 함수

    젯팩 6 의 시스템 OpenCV 는 4.5.4 라 구버전 API 를 씁니다. 반면
    개발용 PC 나 CI 서버는 pip 로 최신 버전을 깔기 때문에 신버전
    API 를 씁니다. 양쪽에서 같은 코드가 돌아야 하므로 여기서 흡수합니다.

    Returns:
        gray 이미지를 받아 (corners, ids, rejected) 를 돌려주는 함수
    """
    if hasattr(cv2.aruco, "ArucoDetector"):
        dictionary = cv2.aruco.getPredefinedDictionary(dictionary_id)
        detector = cv2.aruco.ArucoDetector(dictionary, cv2.aruco.DetectorParameters())
        return detector.detectMarkers

    dictionary = cv2.aruco.Dictionary_get(dictionary_id)
    parameters = cv2.aruco.DetectorParameters_create()

    def detect(gray):
        return cv2.aruco.detectMarkers(gray, dictionary, parameters=parameters)

    return detect


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

    # 여기서 반드시 정규화해서 내보냅니다. 아래를 보세요.
    return distance_m, normalize_yaw(yaw_deg)


def normalize_yaw(yaw_deg):
    """정면 기준 기울기를 0 중심으로 접습니다.

    solvePnP 가 돌려주는 회전은 마커를 정면에서 볼 때 180도 근처입니다.
    객체 좌표계는 Y축이 위, 영상 좌표계는 Y축이 아래라 X축 기준 180도
    뒤집힘이 항상 끼기 때문입니다.

    이 정규화를 빼먹으면 -173도 같은 값이 제어기로 흘러가서,
    abs(yaw) < 10 정렬 판정이 영원히 성립하지 않고 근거리에서 각속도가
    상한까지 잘못된 방향으로 나갑니다. 실제로 그렇게 한 번 당했습니다.
    """
    if yaw_deg > 90.0:
        return yaw_deg - 180.0
    if yaw_deg < -90.0:
        return yaw_deg + 180.0
    return yaw_deg
