"""도킹 접근 제어 검증 (legacy/vision/auto_docking_vision.py P제어 이식분)."""

from dataclasses import replace

import pytest

from potner_docking.approach_controller import DockingGains, compute

GAINS = DockingGains()


def test_거리와_정렬이_모두_맞으면_도킹_완료():
    command = compute(0.14, 5.0, 2.0, GAINS)
    assert command.docked is True
    assert command.linear == 0.0
    assert command.angular == 0.0


def test_멀면_전진한다():
    command = compute(1.0, 0.0, 0.0, GAINS)
    assert command.docked is False
    assert command.linear == pytest.approx(GAINS.approach_speed)


def test_마커가_오른쪽에_있으면_오른쪽으로_돈다():
    """angular.z 는 REP-103 대로 **왼쪽(CCW)이 +** 다.

    그래서 "오른쪽으로 돈다" 는 angular < 0 이다. 예전에는 이 단언이
    `> 0` 으로 적혀 있어서, 이름은 맞는데 값이 반대인 채로 CI 를
    통과했다. 그 사이 실기에서는 제어가 마커를 화면 밖으로 밀어냈다.
    """
    command = compute(1.0, 100.0, 0.0, GAINS)
    assert command.angular < 0


def test_마커가_왼쪽에_있으면_왼쪽으로_돈다():
    command = compute(1.0, -100.0, 0.0, GAINS)
    assert command.angular > 0


def test_횡방향_보정은_음의_되먹임이다():
    """오차가 커지면 반대 방향으로 더 세게 돌아야 한다.

    부호가 뒤집히면 이득을 아무리 낮춰도 발산만 느려질 뿐이라, 게인
    튜닝으로는 절대 못 고친다. 그래서 부호 자체를 시험으로 못 박는다.
    """
    small = compute(1.0, 50.0, 0.0, GAINS).angular
    large = compute(1.0, 150.0, 0.0, GAINS).angular

    assert small < 0 and large < 0
    assert large < small  # 오차가 클수록 더 세게(음수로 더 크게) 돈다


def test_멀리서는_기울기를_무시한다():
    """원거리에서 기울기까지 보면 제어가 출렁입니다."""
    far = compute(1.0, 0.0, 30.0, GAINS)
    assert far.angular == pytest.approx(0.0)


def test_가까워지면_기울기도_보정한다():
    near = compute(0.30, 0.0, 30.0, GAINS)
    assert near.angular != pytest.approx(0.0)


def test_거리는_됐는데_정렬이_안_되면_제자리에서_돈다():
    """차동 구동이라 가능한 동작입니다. 애커만 차량에서는 안 됐습니다."""
    command = compute(0.14, 100.0, 0.0, GAINS)
    assert command.docked is False
    assert command.linear == 0.0
    assert command.angular != 0.0


def test_각속도에_상한이_걸린다():
    command = compute(1.0, 100000.0, 0.0, GAINS)
    assert abs(command.angular) <= GAINS.max_angular


# --- 가장자리 보호 ---
#
# 놓치고 나서 찾는 것보다 안 놓치는 것이 훨씬 싸다. 한 번 놓치면 정지
# 대기 2초에 탐색 회전까지 붙고, 실기에서 그 회전이 다시 마커를 밀어냈다.


def test_가장자리로_밀려가면_늦춘다():
    """가까워질수록 시야 여유가 급격히 좁아지는 것이 유실의 근본 원인이다.

    좌우 한계가 0.5m 에서 약 250px 인데 목표 거리 0.15m 에서는 172px 까지
    좁아져서, 같은 자세로 다가가기만 해도 어느 순간 밖으로 나간다. 늦추면
    같은 거리를 좁히는 동안 중앙으로 되돌릴 시간을 더 번다.
    """
    centered = compute(0.40, 10.0, 0.0, GAINS)
    near_edge = compute(0.40, 200.0, 0.0, GAINS)

    assert near_edge.linear < centered.linear
    assert near_edge.linear > 0.0  # 멈추지는 않는다


def test_가장자리에서도_전진을_멈추지는_않는다():
    """멈추면 기울기 보정과 균형이 맞는 지점에서 못 나오는 교착이 생긴다.

    그 교착은 목표 거리 밖이라 align_timeout 도 안 잡아줘서 90초를 태운다.
    """
    stuck = compute(0.40, 100000.0, 0.0, GAINS)

    assert stuck.linear == pytest.approx(
        GAINS.approach_speed * GAINS.edge_min_speed_ratio
    )


def test_가장자리에서는_조준점을_접는다():
    """조준점은 마커를 일부러 옆으로 밀어 두는 항이라, 이미 가장자리로
    가 있으면 그대로 화면 밖으로 밀어낸다."""
    aim_only = replace(GAINS, kp_yaw=0.0)

    # 좌우 오차는 같고 조준점만 다른 두 경우를 비교한다.
    at_edge = compute(0.30, GAINS.edge_guard_px, 20.0, aim_only).angular
    no_aim = replace(aim_only, aim_offset_px_per_deg=0.0)
    without = compute(0.30, GAINS.edge_guard_px, 20.0, no_aim).angular

    assert at_edge == pytest.approx(without)


# --- 조준점 오프셋 ---
#
# 마커를 화면 정중앙이 아니라 기울기 반대쪽으로 비껴 잡아, 비스듬히
# 출발해도 접근 곡선이 마커 법선 축 위로 펴지게 하는 항.


def test_비스듬히_보이면_기울기가_풀리는_쪽으로_추가로_돈다():
    """축의 왼쪽에서 접근 중이면(yaw 음수) 오른쪽으로 파고들어야 한다.

    정중앙만 당기는 제어는 마커에는 도착하지만 법선 축 밖에서 도착하고,
    도착 후에는 제자리 회전뿐이라 횡 오프셋을 못 지운다 — 실기에서
    "수직이면 잘 붙는데 비스듬하면 정확도가 떨어진다"로 나타났다.
    """
    centered_only = replace(GAINS, aim_offset_px_per_deg=0.0, kp_yaw=0.0)
    with_aim = replace(GAINS, kp_yaw=0.0)

    assert compute(0.30, 0.0, -20.0, centered_only).angular == pytest.approx(0.0)
    assert compute(0.30, 0.0, -20.0, with_aim).angular < 0.0
    assert compute(0.30, 0.0, 20.0, with_aim).angular > 0.0


def test_조준점은_가까이에서만_켠다():
    """0.6m 부터 44mm 마커의 yaw 추정이 부호까지 튄다 (pose ambiguity).

    그 노이즈가 조준점에 직결되면 마커를 화면 밖으로 밀어낸다 —
    시뮬레이션에서 게이트 없는 조준점은 30도 출발을 오히려 악화시켰다.
    """
    far = compute(0.70, 0.0, 30.0, GAINS)
    assert far.angular == pytest.approx(0.0)


def test_조준점은_목표_거리_안에서는_끈다():
    """조준점은 굴러가는 동안 접근 곡선을 펴는 항이다. 제자리 정렬에서는
    기여가 없고, 오히려 정렬 평형을 화면 중심 밖으로 옮겨 마커를 프레임
    끝까지 민다 — 명령 지연 0.2초를 넣은 적대 시뮬레이션에서 이 경로로
    비스듬 출발 정렬이 실패했다."""
    aim_only = replace(GAINS, kp_yaw=0.0)

    in_place = compute(0.14, 0.0, 20.0, aim_only)

    assert in_place.angular == pytest.approx(0.0)


def test_조준점_오프셋에는_상한이_있다():
    aim_only = replace(GAINS, kp_yaw=0.0)

    at_limit = compute(0.30, 0.0, 20.0, aim_only).angular  # 20*6 = 120px = 상한
    beyond = compute(0.30, 0.0, 60.0, aim_only).angular  # 360px -> 120px 클램프

    assert beyond == pytest.approx(at_limit)


def test_조준점_이득이_0이면_예전_동작_그대로다():
    """안전 롤백 경로. 실기에서 문제가 보이면 yaml 에서 0 으로 끈다."""
    no_aim = replace(GAINS, aim_offset_px_per_deg=0.0)

    command = compute(0.30, 40.0, 20.0, no_aim)

    assert command.angular == pytest.approx(
        -40.0 * GAINS.kp_lateral + 20.0 * GAINS.kp_yaw
    )


def test_제자리_회전에_대해서도_음의_되먹임이다():
    """제자리 회전은 좌우 오차(15.3px/deg)와 기울기(1:1)를 강체로 함께
    움직인다. 조준점 이득이 15.3 - kp_yaw/kp_lateral (= 8.5) 를 넘으면
    이 합성 되먹임의 부호가 뒤집혀 정렬이 발산한다 — 기본값(6)이 그
    안에 있음을 못 박는다. 이득을 올릴 때 이 시험이 깨지면 올리면 안
    되는 값이다.
    """
    px_per_deg = 876.4 * 3.141592653589793 / 180.0  # ~15.3

    before = compute(0.30, 50.0, 5.0, GAINS).angular
    rotated_left = compute(0.30, 50.0 + px_per_deg, 6.0, GAINS).angular

    assert rotated_left < before  # 왼쪽으로 더 돌수록 명령은 더 오른쪽으로
