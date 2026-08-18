"""카메라 초점거리 보정 계산.

ROS에 의존하지 않는 순수 파이썬 모듈입니다. 카메라 없이 CI에서 검증됩니다.

이 파일이 따로 있는 이유가 있습니다. 원래 이 계산은 focal_calibrator_node.py
안에 있었는데 비율이 뒤집혀 있었고, rclpy 를 import 하는 파일이라 CI 가
검사하지 못해서 실기에서 이상한 값(410px, 실제는 876px)이 나올 때까지
드러나지 않았습니다.
"""

import math


def suggest_focal_length(
    current_focal: float, measured_distance: float, true_distance: float
) -> float:
    """실제 거리를 알 때 올바른 초점거리를 역산합니다.

    핀홀 모델에서 거리와 초점거리는 비례합니다.

        측정거리 = 초점거리 x 마커크기 / 픽셀크기

    가정한 초점거리와 참값을 각각 넣고 나누면 마커크기와 픽셀크기가
    약분됩니다.

        측정거리 / 실제거리 = 가정초점거리 / 참초점거리

    따라서 참초점거리 = 가정초점거리 x (실제거리 / 측정거리) 입니다.

    ★ 분자와 분모를 헷갈리기 쉽습니다. **거리를 실제보다 짧게 측정했다면
      초점거리는 가정값보다 커야 합니다.** 마커가 화면에서 예상보다 크게
      보였다는 뜻이고, 그건 렌즈가 더 확대해서 본다는 의미이기 때문입니다.

    Args:
        current_focal: 측정할 때 설정돼 있던 초점거리 (px)
        measured_distance: 그 설정으로 계산된 거리 (m)
        true_distance: 자로 잰 실제 거리 (m)

    Returns:
        권장 초점거리 (px)

    Raises:
        ValueError: 거리나 초점거리가 0 이하일 때
    """
    if current_focal <= 0.0:
        raise ValueError(f"초점거리가 0 이하입니다: {current_focal}")
    if measured_distance <= 0.0:
        raise ValueError(f"측정 거리가 0 이하입니다: {measured_distance}")
    if true_distance <= 0.0:
        raise ValueError(f"실제 거리가 0 이하입니다: {true_distance}")

    return current_focal * (true_distance / measured_distance)


def distance_error_percent(measured_distance: float, true_distance: float) -> float:
    """측정 거리가 실제보다 얼마나 어긋났는지 백분율로 돌려줍니다."""
    if true_distance <= 0.0:
        raise ValueError(f"실제 거리가 0 이하입니다: {true_distance}")
    return (measured_distance - true_distance) / true_distance * 100.0


def horizontal_fov_deg(focal_px: float, image_width: int) -> float:
    """초점거리에서 수평 화각을 구합니다.

    보정 결과가 그럴듯한지 확인하는 데 씁니다. 카메라 사양의 화각과 크게
    다르면 마커 크기를 잘못 넣었거나 계산이 틀린 것입니다. 실제로 이
    확인 덕분에 뒤집힌 공식을 찾았습니다.
    """
    if focal_px <= 0.0:
        raise ValueError(f"초점거리가 0 이하입니다: {focal_px}")
    return math.degrees(2.0 * math.atan((image_width / 2.0) / focal_px))
