"""축간거리(wheel_separation) 실측 보정.

ROS 에 의존하지 않는 순수 모듈입니다.

★ 왜 자로 잰 값으로는 부족한가 — 제자리 회전에서는 타이어가 옆으로
  비벼지기 때문에, **회전에 실제로 작용하는 축간거리**가 바퀴 중심
  사이의 기하학적 거리와 다릅니다. 바닥재와 타이어에 따라 몇 %씩
  차이가 납니다.

  이 값이 틀리면 오도메트리가 회전을 실제와 다르게 셉니다. 그러면
  로봇은 자기가 어느 방향을 보고 있는지 틀리게 알게 되고, 직진 명령을
  줘도 엉뚱한 데로 갑니다 — 회전이 많이 섞인 경로일수록 심해집니다.

  실기에서 180도 회전 명령에 몸이 190도를 돈 것이 이 오차였습니다.

★ 오도메트리로는 이 오차를 절대 못 잡습니다. 명령을 바퀴 속도로 바꿀
  때와 엔코더에서 각도를 되계산할 때 **같은 값을 쓰기 때문에 오차가
  상쇄되어**, 오도메트리는 늘 "명령한 만큼 돌았다"고 보고합니다.
  그래서 사람이 자로 재서 넣어 주는 수밖에 없습니다.
"""

import math


def deviation_from_offsets(
    front_offset_m: float, rear_offset_m: float, length_m: float
) -> float:
    """로봇 앞뒤 끝이 기준선에서 떨어진 거리로 틀어진 각도를 구합니다 (deg).

    각도기 없이 자만으로 재기 위한 것입니다. 회전을 정수 바퀴만큼 시켰다면
    로봇은 원래 방향으로 돌아와 있어야 하므로, 기준선과의 어긋남이 곧
    회전 오차입니다.

    Args:
        front_offset_m: 로봇 앞끝과 기준선 사이 거리 (m). 선 왼쪽이면 +
        rear_offset_m: 로봇 뒤끝과 기준선 사이 거리 (m). 부호 규약은 같음
        length_m: 앞끝에서 뒤끝까지 거리 (m)

    Returns:
        틀어진 각도 (deg). 양수면 반시계(왼쪽)로 더 돌아간 것
    """
    if length_m <= 0.0:
        raise ValueError("로봇 길이는 0보다 커야 합니다.")
    return math.degrees(math.atan2(front_offset_m - rear_offset_m, length_m))


def corrected_separation(
    configured_m: float, odometry_deg: float, actual_deg: float
) -> float:
    """실측 회전량으로 축간거리를 보정합니다 (m).

    오도메트리 각도 = (오른쪽거리 - 왼쪽거리) / 설정_축간거리
    실제 각도       = (오른쪽거리 - 왼쪽거리) / 실제_축간거리

    두 식에서 바퀴가 굴러간 거리는 같으므로:
        실제_축간거리 = 설정_축간거리 x 오도메트리각도 / 실제각도

    즉 **몸이 오도메트리보다 많이 돌았으면 축간거리를 줄여야** 합니다.

    Args:
        configured_m: 지금 설정된 값 (m)
        odometry_deg: 오도메트리가 셌다고 보고한 회전량 (deg)
        actual_deg: 자로 잰 실제 회전량 (deg)
    """
    if configured_m <= 0.0:
        raise ValueError("축간거리는 0보다 커야 합니다.")
    if odometry_deg == 0.0 or actual_deg == 0.0:
        raise ValueError("회전량이 0이면 보정할 수 없습니다.")
    if (odometry_deg > 0.0) != (actual_deg > 0.0):
        raise ValueError("두 회전량의 방향이 반대입니다. 측정을 다시 하세요.")
    return configured_m * odometry_deg / actual_deg


def plausible(configured_m: float, corrected_m: float, tolerance: float = 0.25):
    """보정값이 믿을 만한 범위인지.

    측정을 잘못하면(기준선을 잘못 봤거나 바퀴가 미끄러졌거나) 터무니없는
    값이 나오는데, 그걸 그대로 설정에 넣으면 로봇이 전보다 더 못 갑니다.
    기존 값에서 이 비율 넘게 벗어나면 측정을 의심하라는 뜻입니다.

    Returns:
        (믿을 만한가, 사람이 읽을 설명)
    """
    ratio = corrected_m / configured_m
    if abs(ratio - 1.0) <= tolerance:
        return True, f"기존 값 대비 {(ratio - 1.0) * 100:+.1f}%. 정상 범위입니다."
    return False, (
        f"기존 값 대비 {(ratio - 1.0) * 100:+.1f}% 로 너무 큽니다. "
        "측정을 다시 하세요. 바퀴가 미끄러졌거나 기준선을 잘못 봤을 수 "
        "있습니다."
    )
