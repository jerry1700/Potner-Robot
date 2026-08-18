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
    """도킹 P 제어 게인.

    TODO: 실기에서 재튜닝 — 진동하면 kp 를 낮추고, 굼뜨면 올리세요.
    아래는 애커만 시절 게인을 각속도(rad/s) 기준으로 환산한 출발점입니다.
    """

    kp_lateral: float = 0.0025  # 화면 좌우 오차(px) -> rad/s
    kp_yaw: float = 0.017  # 마커 기울기(deg) -> rad/s
    approach_speed: float = 0.12  # 접근 직진 속도 (m/s)
    max_angular: float = 0.8  # 각속도 상한 (rad/s)

    yaw_blend_distance: float = 0.40  # 이 거리(m) 안쪽부터 기울기 보정 시작
    # 목표 정지 거리 (m). 카메라 렌즈에서 마커 면까지입니다 — 카메라가
    # 회전중심보다 0.110m 앞에 있으므로 몸통 기준으로는 그만큼 더 가깝습니다.
    #
    # ★ 이 값을 줄이면 마커가 화면을 꽉 채워 검출이 끊기기 쉽습니다.
    #   0.15m 에서 44mm 마커는 257px (가로 640 중) 이라 좌우 여유 172px,
    #   상하 여유 19mm 입니다. 0.10m 로 줄이면 386px 가 되어 좌우 114px,
    #   **상하는 5mm** 로 좁아집니다 — 마커 높이를 카메라 광축(바닥
    #   130mm)에 5mm 안으로 맞추지 못하면 위아래가 잘립니다. 실기에서
    #   가능성이 없다고 판단해 0.15m 로 되돌렸습니다.
    target_distance: float = 0.15
    lateral_tolerance: float = 30.0  # 정렬 성공 판정 (px)
    yaw_tolerance: float = 10.0  # 정렬 성공 판정 (deg)

    # ★ 조준점 오프셋 (px/deg) — 비스듬히 출발해도 스테이션 정면으로 돌아
    #   들어가게 하는 이득. 마커를 화면 정중앙이 아니라 기울기(yaw) 반대쪽
    #   으로 이 이득 x yaw 만큼 비껴 잡으면, 접근 곡선이 마커 법선 축 위로
    #   펴집니다. 정중앙만 당기는 제어는 비스듬히 출발하면 축 밖에서
    #   도착하고, 도착 후에는 제자리 회전뿐이라 횡 오프셋을 못 지웁니다.
    #   시뮬레이션(시드 30, 실코드 재검증): 5도 비스듬 출발 도킹률
    #   20~33% -> 97~100%, 10도는 0.7m 출발 기준 0 -> 80% (0.5m 출발
    #   10도부터는 시야 한계라 못 붙고, 대신 align_timeout 이 일찍 접어
    #   재시도 기회를 줍니다). 수직 출발 퇴행 없음. 0 이면 예전 동작
    #   그대로입니다.
    #
    #   ★ 8 이상으로 올리지 마세요. 제자리 정렬에서 회전은 좌우 오차를
    #     15.3px/deg(초점거리 876.4 x pi/180)로, 기울기를 1:1 로 함께
    #     움직이는데, 이 이득이 15.3 - kp_yaw/kp_lateral (= 8.5) 를 넘으면
    #     합성 되먹임의 부호가 뒤집혀 정렬이 발산합니다.
    aim_offset_px_per_deg: float = 6.0
    # 이 거리(m) 밖에서는 조준점을 끕니다. 0.6m 부터는 44mm 마커가 픽셀로
    # 너무 작아 yaw 추정이 부호까지 튀는데(pose ambiguity), 그 노이즈가
    # 조준점에 직결되면 마커를 화면 밖으로 밀어냅니다.
    aim_offset_distance: float = 0.55
    aim_offset_max_px: float = 120.0  # 오프셋 상한 — 마커를 화면 밖으로 안 밀게

    # ★ 가장자리 보호 — 마커가 화면 끝으로 밀려가는 중이면 조준점을 접고
    #   속도를 줄입니다. 놓치고 나서 찾는 것보다 안 놓치는 것이 훨씬 싸기
    #   때문입니다 (한 번 놓치면 정지 대기 2초 + 탐색 회전이 붙습니다).
    #
    #   가까워질수록 시야 여유가 급격히 줄어드는 것이 유실의 근본 원인
    #   입니다. 마커가 화면에 온전히 들어오는 좌우 한계는 거리에 비례해서
    #   0.5m 에서 약 250px 인데 0.2m 에서는 200px, 목표 거리 0.15m 에서는
    #   172px 까지 좁아집니다. 같은 자세로 다가가기만 해도 어느 순간
    #   밖으로 나갑니다.
    #
    #   ★ edge_guard_px 는 목표 거리에서의 그 한계보다 크면 안 됩니다.
    #     크면 보호가 최대로 걸리기 전에 마커가 이미 잘려 나갑니다.
    #
    #   느려지면 제어기가 같은 거리를 좁히는 동안 중앙으로 되돌릴 시간을
    #   더 법니다. 완전히 멈추지는 않습니다 — 멈추면 기울기 보정과 균형이
    #   맞는 지점에서 영영 못 나오는 교착이 생깁니다.
    edge_guard_px: float = 170.0  # 이 좌우 오차에서 보호가 최대
    edge_min_speed_ratio: float = 0.25  # 보호가 최대일 때 남기는 속도 비율

    # 정렬이 끝난 뒤 제자리에서 도는 각도. 화분이 로봇 뒤쪽에 있어서,
    # 마커를 보고 붙은 자세 그대로면 스테이션 장치가 화분에 닿지 않습니다.
    # 0 으로 두면 회전 없이 바로 완료합니다.
    #
    # ★ 이 각도는 오도메트리로 잽니다. 그래서 정확도가 wheel_separation
    #   실측에 통째로 달려 있습니다 — 명령을 바퀴 속도로 바꿀 때와
    #   엔코더에서 각도를 되계산할 때 같은 값을 쓰기 때문에 오차가
    #   상쇄되어, 오도메트리는 늘 "명령한 만큼 돌았다"고 보고합니다.
    #
    #   한때 이 값이 170 이었습니다. wheel_separation 이 자로 잰 0.230
    #   이던 시절 오도메트리가 회전을 5% 적게 세서, 180 을 주면 몸이
    #   190도를 돌았기 때문입니다. 2026-08-10 에 축간거리를 실측
    #   보정(0.2179)해 근본 원인을 없애고 정상값으로 되돌렸습니다.
    #
    #   ★ 다시 어긋나면 이 값이 아니라 축간거리를 재세요. 여기서
    #     보정하면 도킹 회전만 맞고 좌표 주행은 계속 틀어집니다 —
    #     실제로 그렇게 당했습니다.  python3 tools/calibrate_turn.py
    turn_after_dock_deg: float = 180.0
    turn_speed: float = 0.5  # rad/s. + 가 좌회전(CCW)

    # ★ 회전 끝 감속. 정지 명령이 물리 정지가 되기까지 0.2~0.5초가 걸려서
    #   (20Hz 주기 + 시리얼 + PID 감속 + 관성) 전속으로 문턱을 지나면 그
    #   지연이 그대로 초과 회전이 됩니다 — 0.5rad/s 로는 +10도 (실측 190도).
    #   남은 각도가 turn_slow_angle_deg 아래로 내려가면 비례 감속하고,
    #   turn_stop_margin_deg 만큼 일찍 정지 판정합니다. 실코드 시뮬레이션
    #   기준 총지연 0.6초 이하에서 최종각 180~184도, 실측 상당 지연
    #   (0.35초)에서 181도.
    turn_slow_angle_deg: float = 60.0
    # 감속 바닥 (rad/s). 더 낮추면 바퀴가 정지마찰을 못 이겨 목표 직전에
    # 정체할 수 있습니다 (0.15 x 0.115m = 바퀴 17mm/s).
    turn_min_speed: float = 0.15
    turn_stop_margin_deg: float = 2.0


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

    # ★ 부호에 마이너스가 붙는 이유 — 두 규약이 반대입니다.
    #
    #   lateral_error_px : 마커가 화면 **오른쪽**이면 +
    #                      (marker_detector_node.py 의 center_x - width/2)
    #   angular.z        : **왼쪽(CCW)** 회전이 + (REP-103, kinematics.py 의
    #                      right = linear + angular * half_track)
    #
    # 오른쪽에 있는 마커를 가운데로 데려오려면 오른쪽으로 돌아야 하고,
    # 그건 angular 가 음수라는 뜻입니다. 마이너스를 빼면 양의 되먹임이
    # 되어 마커를 화면 밖으로 밀어냅니다 — 루프 이득이
    # kp_lateral x 초점거리 = 0.0025 x 876.4 = 2.19/s 라 **0.32초마다 오차가
    # 두 배**가 됩니다. 0.5m 에서 0.15m 까지 오는 동안 64배로 커져서,
    # 처음에 2mm 만 어긋나 있어도 도중에 마커가 프레임을 벗어납니다.
    # 실기에서 "정확히 정면이면 붙고 조금만 틀어지면 마커를 놓친다" 로
    # 나타났습니다.
    #
    # 조준점: 마커가 기울어 보이면(= 법선 축 밖에서 접근 중이면) 정중앙이
    # 아니라 기울기 반대쪽의 aim_px 를 조준합니다. 게이트·상한·이득 상한의
    # 이유는 DockingGains 의 aim_offset_* 주석에 있습니다.
    #
    # ★ 목표 거리 안(제자리 정렬)에서는 끕니다. 조준점은 굴러가는 동안
    #   접근 곡선을 펴는 항이라 제자리 회전에는 기여가 없고, 오히려 정렬
    #   평형을 화면 중심 밖으로 옮겨 마커를 프레임 끝까지 밀 수 있습니다
    #   (명령→물리 0.2초 지연을 넣은 적대 시뮬레이션에서 이 경로로 정렬이
    #   실패했습니다).
    # 마커가 화면 끝으로 밀려간 정도. 0 이면 중앙, 1 이면 보호 최대.
    edge = 0.0
    if gains.edge_guard_px > 0.0:
        edge = min(abs(lateral_error_px) / gains.edge_guard_px, 1.0)

    aim_px = 0.0
    if gains.target_distance < distance_m < gains.aim_offset_distance:
        aim_px = _clamp(
            yaw_error_deg * gains.aim_offset_px_per_deg, gains.aim_offset_max_px
        )
        # 조준점은 마커를 일부러 옆으로 밀어 두는 항이라, 이미 가장자리로
        # 가 있으면 그대로 화면 밖으로 밀어냅니다. 중앙 복귀가 우선입니다.
        aim_px *= 1.0 - edge

    angular = -(lateral_error_px - aim_px) * gains.kp_lateral

    # 멀리서는 화면 중앙을 맞추는 데만 집중합니다. 원거리에서 기울기까지
    # 보면 제어가 출렁입니다.
    #
    # ★ 이쪽은 **뒤집지 마세요.** 위의 마이너스와 짝을 이뤄야 합니다.
    #   둘 다 음수면 새들점이 되어 다시 발산하고, yaw 항을 지우면 위치는
    #   맞아도 자세(법선 대비 각도)가 영영 안 맞습니다. 이 항이 법선
    #   이탈각을 0 으로 끌어내리는 유일한 경로입니다.
    if distance_m < gains.yaw_blend_distance:
        angular += yaw_error_deg * gains.kp_yaw

    angular = _clamp(angular, gains.max_angular)

    if close_enough:
        return DockingCommand(0.0, angular, False, "제자리 미세 정렬")

    # 가장자리로 밀려가 있으면 늦춰서 중앙으로 되돌릴 시간을 법니다.
    speed = gains.approach_speed * (1.0 - (1.0 - gains.edge_min_speed_ratio) * edge)
    reason = "가장자리 — 늦추며 접근" if edge > 0.5 else "접근 중"

    return DockingCommand(speed, angular, False, reason)


def pixel_to_lateral_error(marker_corners, image_width: float) -> float:
    """마커 네 꼭짓점의 평균 x좌표와 화면 중심의 차이를 구합니다."""
    center_x = sum(corner[0] for corner in marker_corners) / len(marker_corners)
    return center_x - (image_width / 2.0)


# 기울기 정규화는 값이 만들어지는 곳에서 합니다.
# potner_perception.marker_pose.normalize_yaw 참고. 두 곳에서 접으면 어느
# 쪽이 원본인지 알 수 없어져서 일부러 여기 두지 않았습니다.


def _clamp(value: float, limit: float) -> float:
    return math.copysign(min(abs(value), limit), value)
