"""ArUco 마커 거리·각도 추정 검증.

여기서 잡으려는 문제가 둘입니다.

1. OpenCV 버전별 ArUco API 차이
   4.7 에서 API 가 바뀌었습니다. 젯팩 6 의 시스템 OpenCV 는 4.5.4,
   개발 PC 와 CI 는 pip 최신 버전이라 서로 다른 API 를 씁니다.
   한쪽에서만 도는 코드가 들어가는 걸 막습니다.

2. 거리 추정이 실제로 맞는지
   도킹 정확도가 여기서 결정됩니다.
"""

import cv2
import numpy as np
import pytest

from potner_perception.marker_pose import (
    DEFAULT_DICTIONARY,
    CameraIntrinsics,
    create_detector,
    estimate_pose,
)


def _draw_marker(dictionary_id, marker_id, side_px):
    """OpenCV 버전에 상관없이 마커 이미지를 만듭니다."""
    if hasattr(cv2.aruco, "generateImageMarker"):  # 4.7 이상
        dictionary = cv2.aruco.getPredefinedDictionary(dictionary_id)
        return cv2.aruco.generateImageMarker(dictionary, marker_id, side_px)
    dictionary = cv2.aruco.Dictionary_get(dictionary_id)  # 4.5 이하
    return cv2.aruco.drawMarker(dictionary, marker_id, side_px)


def _square_corners(center_x, center_y, half_px):
    """aruco 가 주는 순서(좌상 - 우상 - 우하 - 좌하)로 정사각형 꼭짓점."""
    return np.array(
        [
            [center_x - half_px, center_y - half_px],
            [center_x + half_px, center_y - half_px],
            [center_x + half_px, center_y + half_px],
            [center_x - half_px, center_y + half_px],
        ],
        dtype=np.float32,
    )


def test_탐지기가_어떤_OpenCV_버전에서도_만들어진다():
    detect = create_detector()
    assert callable(detect)


@pytest.mark.parametrize("marker_id", [1, 2, 3, 4])
def test_스테이션_마커_4종을_모두_읽는다(marker_id):
    """src/potner_docking/markers/ 의 마커 4장에 대응합니다."""
    image = _draw_marker(DEFAULT_DICTIONARY, marker_id, 240)
    # 마커 주변에 흰 여백이 없으면 탐지가 실패합니다. 실물 인쇄도 마찬가지입니다.
    padded = cv2.copyMakeBorder(image, 60, 60, 60, 60, cv2.BORDER_CONSTANT, value=255)

    corners, ids, _ = create_detector()(padded)

    assert ids is not None, "마커를 하나도 못 찾았습니다"
    assert marker_id in ids.flatten()


def test_거리_추정이_핀홀_모델과_맞는다():
    """5cm 마커가 화면에서 60px 로 보이면 초점거리 600px 기준 50cm."""
    intrinsics = CameraIntrinsics(focal_length_px=600.0, center_x=320.0, center_y=240.0)
    corners = _square_corners(320.0, 240.0, 30.0)  # 한 변 60px

    distance_m, yaw_deg = estimate_pose(corners, intrinsics, marker_size=0.05)

    assert distance_m == pytest.approx(0.5, rel=0.02)
    assert yaw_deg == pytest.approx(0.0, abs=1.0)


def test_마커가_작게_보이면_멀다():
    intrinsics = CameraIntrinsics(focal_length_px=600.0, center_x=320.0, center_y=240.0)

    near, _ = estimate_pose(_square_corners(320, 240, 60), intrinsics, 0.05)
    far, _ = estimate_pose(_square_corners(320, 240, 15), intrinsics, 0.05)

    assert near < far
    assert near == pytest.approx(0.25, rel=0.02)
    assert far == pytest.approx(1.0, rel=0.02)


def test_마커_크기를_잘못_넣으면_거리가_그만큼_틀어진다():
    """인쇄한 마커를 자로 재라고 하는 이유. 크기가 2배면 거리도 2배로 나옵니다."""
    intrinsics = CameraIntrinsics(focal_length_px=600.0)
    corners = _square_corners(320, 240, 30)

    correct, _ = estimate_pose(corners, intrinsics, marker_size=0.05)
    wrong, _ = estimate_pose(corners, intrinsics, marker_size=0.10)

    assert wrong == pytest.approx(correct * 2, rel=0.02)
