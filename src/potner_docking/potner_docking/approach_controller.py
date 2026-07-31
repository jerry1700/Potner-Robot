"""ArUco 마커 기반 정밀 접근 제어 (비례 제어).

legacy/vision/auto_docking_vision.py 의 P 제어를 옮기되, 애커만 조향(서보
각도)에서 차동 구동(각속도 rad/s)으로 출력 형태를 바꿨습니다.

차동 구동으로 바뀌면서 생긴 가장 큰 변화는 제자리 회전이 가능해졌다는
점입니다. 기존 코드에서 linear=0 으로 두고 조향만 주던 미세 정렬 구간은
애커만 차량에서는 바퀴만 꺾이고 움직이지 않아 사실상 죽은 코드였습니다.

ROS에 의존하지 않는 순수 파이썬 모듈입니다.
"""

import math
from dataclasses import dataclass


@dataclass
class DockingGains:
    """TODO: 실기에서 재튜닝. 진동하면 kp 를 낮추고, 굼뜨면 올리세요.

    아래는 애커만 시절 게인을 각속도(rad/s) 기준으로 환산한 출발점입니다.
    """

    kp_lateral: float = 0.0025  # 화면 좌우 오차(px) -> rad/s
    kp_yaw: float = 0.017  # 마커 기울기(deg) -> rad/s
    approach_speed: float = 0.12  # 접근 직진 속도 (m/s)
    max_angular: float = 0.8  # 각속도 상한 (rad/s)

    yaw_blend_distance: float = 0.40  # 이 거리(m) 안쪽부터 기울기 보정 시작
    target_distance: float = 0.15  # 목표 정지 거리 (m)
    lateral_tolerance: float = 30.0  # 정렬 성공 판정 (px)
    yaw_tolerance: float = 10.0  # 정렬 성공 판정 (deg)


@dataclass
class DockingCommand:
    linear: float
    angular: float
    docked: bool
    reason: str


def compute(
    distance_m: float,
    lateral_error_px: float,
    yaw_error_deg: float,
    gains: DockingGains = None,
) -> DockingCommand:
    """마커 관측값으로 다음 주행 명령을 계산합니다.

    Args:
        distance_m: 마커까지 거리 (m)
        lateral_error_px: 화면 중심 대비 마커 중심의 좌우 오차 (px, 오른쪽이 +)
        yaw_error_deg: 마커 평면의 기울어짐 (deg, 0이면 정면으로 마주봄)
    """
    gains = gains or DockingGains()

    close_enough = distance_m <= gains.target_distance
    centered = abs(lateral_error_px) < gains.lateral_tolerance
    parallel = abs(yaw_error_deg) < gains.yaw_tolerance

    if close_enough and centered and parallel:
        return DockingCommand(0.0, 0.0, True, "정렬 완료")

    angular = lateral_error_px * gains.kp_lateral

    # 멀리서는 화면 중앙을 맞추는 데만 집중합니다. 원거리에서 기울기까지
    # 보면 제어가 출렁입니다.
    if distance_m < gains.yaw_blend_distance:
        angular += yaw_error_deg * gains.kp_yaw

    angular = _clamp(angular, gains.max_angular)

    if close_enough:
        return DockingCommand(0.0, angular, False, "제자리 미세 정렬")

    return DockingCommand(gains.approach_speed, angular, False, "접근 중")


def pixel_to_lateral_error(marker_corners, image_width: float) -> float:
    """마커 네 꼭짓점의 평균 x좌표와 화면 중심의 차이를 구합니다."""
    center_x = sum(corner[0] for corner in marker_corners) / len(marker_corners)
    return center_x - (image_width / 2.0)


# 기울기 정규화는 값이 만들어지는 곳에서 합니다.
# potner_perception.marker_pose.normalize_yaw 참고. 두 곳에서 접으면 어느
# 쪽이 원본인지 알 수 없어져서 일부러 여기 두지 않았습니다.


def _clamp(value: float, limit: float) -> float:
    return math.copysign(min(abs(value), limit), value)
