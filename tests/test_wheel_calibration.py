"""축간거리 실측 보정 검증.

부호를 뒤집으면 보정이 오차를 두 배로 키운다. 실기에서 그걸 알아채려면
로봇을 몇 바퀴 더 돌려봐야 하므로, 방향 자체를 시험으로 못 박는다.
"""

import math

import pytest

from potner_base.wheel_calibration import (
    corrected_separation,
    deviation_from_offsets,
    plausible,
)


# --- 자로 잰 값 -> 각도 ---


def test_앞뒤가_같이_떨어져_있으면_틀어진_게_아니다():
    """로봇이 옆으로 평행이동만 한 경우다. 각도는 0 이어야 한다."""
    assert deviation_from_offsets(0.05, 0.05, 0.30) == pytest.approx(0.0)


def test_앞이_더_왼쪽이면_반시계로_더_돈_것이다():
    assert deviation_from_offsets(0.05, -0.05, 0.30) > 0


def test_앞이_더_오른쪽이면_시계로_더_돈_것이다():
    assert deviation_from_offsets(-0.05, 0.05, 0.30) < 0


def test_각도가_삼각비와_맞는다():
    """앞뒤 차이 0.30m, 길이 0.30m 면 정확히 45도."""
    assert deviation_from_offsets(0.15, -0.15, 0.30) == pytest.approx(45.0)


def test_길이가_0이면_거부한다():
    with pytest.raises(ValueError):
        deviation_from_offsets(0.05, 0.0, 0.0)


# --- 각도 -> 축간거리 ---


def test_몸이_더_돌았으면_축간거리를_줄인다():
    """실기 증상 그대로 — 180도를 명령했는데 몸은 190도를 돌았다."""
    corrected = corrected_separation(0.230, 180.0, 190.0)

    assert corrected < 0.230
    assert corrected == pytest.approx(0.230 * 180.0 / 190.0)


def test_몸이_덜_돌았으면_축간거리를_키운다():
    assert corrected_separation(0.230, 180.0, 170.0) > 0.230


def test_정확하면_그대로다():
    assert corrected_separation(0.230, 720.0, 720.0) == pytest.approx(0.230)


def test_보정은_회전량에_비례하지_않는다():
    """같은 비율의 오차라면 몇 바퀴를 돌든 같은 보정값이 나와야 한다.
    안 그러면 측정 바퀴 수에 따라 답이 달라진다."""
    one = corrected_separation(0.230, 360.0, 380.0)
    three = corrected_separation(0.230, 1080.0, 1140.0)

    assert one == pytest.approx(three)


def test_회전_방향이_반대면_거부한다():
    """측정을 잘못한 것이다. 그대로 계산하면 음수 축간거리가 나온다."""
    with pytest.raises(ValueError):
        corrected_separation(0.230, 720.0, -700.0)


def test_회전량이_0이면_거부한다():
    with pytest.raises(ValueError):
        corrected_separation(0.230, 0.0, 10.0)


# --- 터무니없는 값 거르기 ---


def test_작은_보정은_받아들인다():
    ok, _ = plausible(0.230, 0.230 * 180.0 / 190.0)  # 약 -5%
    assert ok is True


def test_큰_보정은_의심한다():
    """바퀴가 미끄러졌거나 기준선을 잘못 본 것이다. 그대로 넣으면
    로봇이 전보다 더 못 간다."""
    ok, note = plausible(0.230, 0.100)

    assert ok is False
    assert "다시" in note


def test_설명에_변화율이_들어간다():
    _, note = plausible(0.230, 0.230 * 1.05)
    assert "+5" in note


@pytest.mark.parametrize("turns", [1, 3, 5])
def test_실기_시나리오_전체가_이어진다(turns):
    """자로 잰 값에서 새 축간거리까지 한 번에 흐르는지."""
    odometry = 360.0 * turns
    # 앞끝 +4cm, 뒤끝 -4cm, 길이 30cm -> 약 15도 더 돌았다
    deviation = deviation_from_offsets(0.04, -0.04, 0.30)
    corrected = corrected_separation(0.230, odometry, odometry + deviation)

    assert corrected < 0.230
    assert math.isfinite(corrected)
