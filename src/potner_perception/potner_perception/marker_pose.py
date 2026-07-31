"""ArUco 마커의 거리와 기울기 추정.

legacy/vision/auto_docking_vision.py 의 estimate_distance_and_angle 을 옮긴
계산 모듈입니다. cv2와 numpy만 쓰고 ROS에는 의존하지 않습니다.

거리 단위를 cm에서 m로 바꿨습니다. ROS는 SI 단위(m, rad, s)를 쓰는 게
규칙이고, 여기서 cm를 흘리면 Nav2와 TF 전체가 100배 틀어집니다.
"""

from dataclasses import dataclass

import cv2
import numpy as np

# 인쇄한 마커의 검은 사각형 한 변 실측값 (m).
# 마커를 다시 인쇄하면 자로 재서 이 값과 potner_params.yaml 을 함께 고치세요.
# 이 값이 틀리면 거리가 그 비율만큼 통째로 어긋납니다.
DEFAULT_MARKER_SIZE = 0.044

# beacon 이미지를 뽑을 때 쓴 사전. 바꾸면 기존 마커를 못 읽습니다.
DEFAULT_DICTIONARY = cv2.aruco.DICT_6X6_250

# focal_calibrator 로 실측한 값 (BRIO 100, 640x480, 2026-07-28).
# 카메라나 해상도를 바꾸면 다시 재세요. 이 값이 틀리면 거리뿐 아니라
# solvePnP 가 내는 기울기까지 어긋나 정렬 판정이 흔들립니다.
#
# 숫자를 여러 곳에 적으면 한쪽만 고쳐서 어긋납니다. 노드의 파라미터
# 기본값도 이 상수를 참조하고, potner_params.yaml 과 일치하는지는
# tests/test_config_consistency.py 가 확인합니다.
DEFAULT_FOCAL_LENGTH_PX = 876.4


@dataclass
class CameraIntrinsics:
    """카메라 내부 파라미터."""

    focal_length_px: float = DEFAULT_FOCAL_LENGTH_PX
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
        """왜곡 계수를 0으로 둡니다.

        두 거리에서 실측해 이 가정이 타당함을 확인했습니다 (2026-07-28).
        50cm 에서 -0.2%, 15cm 에서 +0.2% 로 **부호가 갈립니다.** 렌즈 왜곡이
        있으면 거리에 따라 한 방향으로 누적되므로, 부호가 반대인 것은 계통
        오차가 없다는 뜻입니다. 남은 오차는 측정 노이즈입니다.

        도킹은 15~50cm 범위에서만 쓰므로 체커보드 캘리브레이션까지 갈
        필요가 없습니다. 더 먼 거리를 쓰게 되면 그때 다시 확인하세요.
        """
        return np.zeros((4, 1), dtype=np.float32)


def create_detector(dictionary_id: int = DEFAULT_DICTIONARY):
    """OpenCV 버전에 상관없이 동작하는 마커 탐지 함수를 돌려줍니다.

    ArUco 파이썬 API는 OpenCV 4.7에서 한 번 갈아엎어졌습니다.

        4.7 이상   getPredefinedDictionary + ArucoDetector 객체
        4.5 이하   Dictionary_get + detectMarkers 함수

    젯팩 6의 시스템 OpenCV는 4.5.4라 구버전 API를 씁니다. 반면 개발 PC와
    CI는 pip 최신 버전이라 신버전 API를 씁니다. 양쪽에서 같은 코드가
    돌아야 하므로 여기서 흡수합니다.

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


def estimate_pose(
    corners,
    intrinsics: CameraIntrinsics = None,
    marker_size: float = DEFAULT_MARKER_SIZE,
):
    """마커 네 꼭짓점으로 거리와 좌우 기울기를 계산합니다.

    Args:
        corners: (4, 2) 픽셀 좌표. cv2.aruco 가 주는 순서 그대로
        marker_size: 마커 한 변의 실제 길이 (m)

    Returns:
        (거리 m, 기울기 deg). 추정 실패 시 (None, None)
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

    rotation, _ = cv2.Rodrigues(rvec)
    yaw_deg = float(np.degrees(np.arctan2(rotation[0, 2], rotation[2, 2])))

    return float(tvec[2][0]), normalize_yaw(yaw_deg)


def normalize_yaw(yaw_deg: float) -> float:
    """정면 기준 기울기를 0 중심으로 접습니다.

    solvePnP 가 돌려주는 회전은 마커를 정면에서 볼 때 180도 근처입니다.
    객체 좌표계는 Y축이 위, 영상 좌표계는 Y축이 아래라 X축 기준 180도
    뒤집힘이 항상 끼기 때문입니다.

    이 정규화를 빼먹으면 -173도 같은 값이 제어기로 흘러가서 정렬 판정이
    영원히 성립하지 않고, 근거리에서 각속도가 상한까지 잘못된 방향으로
    나갑니다. 실제로 그렇게 한 번 당했습니다.
    """
    if yaw_deg > 90.0:
        return yaw_deg - 180.0
    if yaw_deg < -90.0:
        return yaw_deg + 180.0
    return yaw_deg
