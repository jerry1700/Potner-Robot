"""초점거리 보정 계산 검증.

이 테스트가 없어서 뒤집힌 비율이 실기까지 갔습니다. 계산이 노드 파일
안에 있어 CI 가 검사할 수 없었고, 실제 카메라로 재보니 410px 이 나왔는데
참값은 876px 이었습니다.

방향을 틀리면 값이 그럴듯하게 나와서 눈으로는 알아채기 어렵습니다.
그래서 부호가 아니라 **방향**을 검증합니다.
"""

import pytest

from potner_perception.calibration import (
    distance_error_percent,
    horizontal_fov_deg,
    suggest_focal_length,
)


def test_거리를_짧게_측정했으면_초점거리를_키워야_한다():
    """방향이 뒤집혀도 값은 그럴듯하게 나옵니다. 이게 실기까지 간 버그입니다.

    마커가 화면에서 예상보다 크게 보였다는 뜻이고, 그건 렌즈가 더 확대해서
    본다는 의미이므로 초점거리는 가정값보다 커야 합니다.
    """
    suggested = suggest_focal_length(
        current_focal=600.0, measured_distance=0.3423, true_distance=0.5
    )

    assert suggested > 600.0
    assert suggested == pytest.approx(876.4, rel=0.001)


def test_거리를_길게_측정했으면_초점거리를_줄여야_한다():
    suggested = suggest_focal_length(
        current_focal=600.0, measured_distance=0.7, true_distance=0.5
    )

    assert suggested < 600.0
    assert suggested == pytest.approx(428.6, rel=0.001)


def test_이미_맞으면_그대로다():
    assert suggest_focal_length(600.0, 0.5, 0.5) == pytest.approx(600.0)


def test_보정값을_적용하면_오차가_사라진다():
    """실기 데이터로 왕복 검증. 권장값을 넣고 다시 재면 실제 거리가 나와야 합니다."""
    current, measured, true = 600.0, 0.3423, 0.5

    corrected = suggest_focal_length(current, measured, true)
    # 같은 관측(픽셀 크기)에 새 초점거리를 적용하면 거리가 이렇게 바뀝니다.
    redone = measured * (corrected / current)

    assert redone == pytest.approx(true, rel=1e-6)


def test_두_번_보정해도_같은_값에_머문다():
    """수렴하지 않으면 공식이 틀린 것입니다."""
    first = suggest_focal_length(600.0, 0.3423, 0.5)
    second = suggest_focal_length(first, 0.5, 0.5)

    assert second == pytest.approx(first)


@pytest.mark.parametrize(
    "current,measured,true",
    [(0.0, 0.5, 0.5), (-1.0, 0.5, 0.5), (600.0, 0.0, 0.5), (600.0, 0.5, 0.0)],
)
def test_잘못된_입력을_거부한다(current, measured, true):
    with pytest.raises(ValueError):
        suggest_focal_length(current, measured, true)


def test_오차_백분율():
    assert distance_error_percent(0.3423, 0.5) == pytest.approx(-31.54, abs=0.01)
    assert distance_error_percent(0.5, 0.5) == pytest.approx(0.0)
    assert distance_error_percent(0.6, 0.5) == pytest.approx(20.0)


def test_화각으로_결과가_그럴듯한지_본다():
    """BRIO 100 은 16:9 대각 58도. 640x480 으로 잘라내면 대각이 그보다
    작아지므로, 876px 의 수평 40도는 사양과 맞습니다. 반면 뒤집힌 값
    410px 은 수평 76도로 사양보다 훨씬 넓어 이상함이 드러납니다."""
    assert horizontal_fov_deg(876.4, 640) == pytest.approx(40.1, abs=0.2)
    assert horizontal_fov_deg(410.8, 640) == pytest.approx(75.8, abs=0.2)


def test_초점거리가_길면_화각이_좁다():
    assert horizontal_fov_deg(1200.0, 640) < horizontal_fov_deg(600.0, 640)
