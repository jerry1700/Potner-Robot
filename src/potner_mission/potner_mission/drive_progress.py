"""수동 주행 한 스텝이 목표량을 채웠는지 오도메트리로 판정하는 순수 로직.

ROS에 의존하지 않으므로 CI에서 검증됩니다.

시간으로 끊으면 정확할 수 없습니다. 피드포워드가 목표 속도를 조금 넘기고
PID가 그걸 깎아내기 전에 시간이 끝나기 때문입니다 — 실기에서 22.5도를
명령했더니 오도메트리로 29.9도(+33%)가 나왔습니다. 그래서 시간이 아니라
**실제로 돌아간 각도·간 거리**를 보고 끊습니다.

★ 오도메트리가 못 오거나 바퀴가 헛돌면 목표량이 영원히 안 채워집니다.
  그래서 호출하는 쪽이 반드시 시간 상한을 함께 걸어야 합니다
  (drive_node 의 progress_timeout_factor).
"""

import math


def yaw_from_quaternion(x: float, y: float, z: float, w: float) -> float:
    """쿼터니언에서 yaw(rad)를 뽑는다."""
    return math.atan2(2.0 * (w * z + x * y), 1.0 - 2.0 * (y * y + z * z))


def shortest_angle_diff(target: float, source: float) -> float:
    """``source`` 에서 ``target`` 까지의 최단 각도 차 (rad, -pi~pi).

    단순 뺄셈을 쓰면 ±pi 경계를 넘는 순간 6.28 이 튀어나와 회전이 즉시
    끝난 것처럼 판정됩니다.
    """
    return math.atan2(
        math.sin(target - source), math.cos(target - source)
    )


def turn_progress(start_yaw: float, current_yaw: float) -> float:
    """제자리 회전 진행량 (rad, 항상 양수).

    최단각을 쓰므로 **한 스텝이 180도 미만일 때만** 유효합니다. 앱 버튼은
    22.5도라 문제없지만, 목표를 180도 이상으로 키우면 이 함수를 각도 누적
    방식으로 바꿔야 합니다.
    """
    return abs(shortest_angle_diff(current_yaw, start_yaw))


def straight_progress(
    start_x: float, start_y: float, current_x: float, current_y: float
) -> float:
    """직진 진행량 (m). 시작점에서의 직선거리."""
    return math.hypot(current_x - start_x, current_y - start_y)
