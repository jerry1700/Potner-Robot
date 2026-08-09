"""발행 주기 구간 평균 검증.

서버가 좌측 구형 적분으로 누적하므로(표본 하나를 다음 표본까지 유지),
로봇이 보내는 값과 시각이 그 적분과 맞물려야 합니다.
"""

from datetime import datetime, timedelta, timezone

import pytest

from potner_bridge.sensor_window import SensorWindow

BASE = datetime(2026, 8, 8, 12, 0, 0, tzinfo=timezone.utc)


def at(seconds):
    return BASE + timedelta(seconds=seconds)


def test_창이_비어_있으면_아무것도_돌려주지_않는다():
    """센서가 빠져 있을 때 값을 지어내면 서버의 covered_seconds 가 실제보다
    높게 잡혀서, 데이터가 부족한 날에도 판정이 나온다."""
    assert SensorWindow().take() is None


def test_구간_평균을_돌려준다():
    window = SensorWindow()
    for index, value in enumerate([100.0, 200.0, 300.0, 400.0, 500.0]):
        window.add(value, at(index * 2))

    mean, _ = window.take()

    assert mean == pytest.approx(300.0)


def test_측정_시각은_창의_첫_표본_시각이다():
    """마지막 표본 시각을 쓰면 그 평균이 다음 창에 적용되어 한 창만큼 밀린다.

    서버는 보고된 시각부터 다음 보고 시각까지 값을 유지하므로, 첫 표본
    시각을 써야 유지 구간과 평균 구간이 겹친다.
    """
    window = SensorWindow()
    window.add(100.0, at(0))
    window.add(900.0, at(8))

    _, measured_at = window.take()

    assert measured_at == at(0)


def test_take_후에는_창이_비워진다():
    """비우지 않으면 다음 창에 이전 표본이 섞여 같은 빛을 두 번 센다."""
    window = SensorWindow()
    window.add(100.0, at(0))
    window.take()

    assert window.count == 0
    assert window.take() is None


def test_다음_창은_자기_첫_표본_시각을_쓴다():
    window = SensorWindow()
    window.add(100.0, at(0))
    window.take()

    window.add(700.0, at(10))
    mean, measured_at = window.take()

    assert mean == pytest.approx(700.0)
    assert measured_at == at(10)


def test_표본이_하나면_그_값_그대로다():
    """발행 주기 안에 측정이 한 번만 들어온 경우도 예전과 같아야 한다."""
    window = SensorWindow()
    window.add(432.0, at(3))

    assert window.take() == (432.0, at(3))


def test_순간적으로_튄_값이_구간_전체를_대표하지_않는다():
    """로봇이 회전해 센서가 잠깐 창을 향하면 한 표본만 크게 튄다.

    예전처럼 마지막값을 보내면 그 순간이 10초치 광량이 되어 5배 증폭된다.
    """
    window = SensorWindow()
    for index in range(4):
        window.add(100.0, at(index * 2))
    window.add(5000.0, at(8))  # 마지막에 튄 값

    mean, _ = window.take()

    assert mean == pytest.approx(1080.0)
    assert mean < 5000.0


def test_0_lux_도_표본으로_센다():
    """밤에는 0 lux 가 정상이다. 0 을 '값 없음' 으로 취급하면 밤 시간대가
    통째로 covered_seconds 에서 빠져 커버리지가 못 미친다."""
    window = SensorWindow()
    window.add(0.0, at(0))
    window.add(0.0, at(2))

    taken = window.take()

    assert taken is not None
    assert taken[0] == pytest.approx(0.0)


def test_reset_은_시각까지_지운다():
    window = SensorWindow()
    window.add(100.0, at(0))
    window.reset()
    window.add(200.0, at(10))

    assert window.take() == (200.0, at(10))
