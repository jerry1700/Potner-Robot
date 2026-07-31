"""센서 원시값 변환 검증.

센서 없이 확인해야 하는 것들입니다. 특히 보정이 안 된 상태를 값으로
흘리지 않는지가 중요합니다. 엉뚱한 급수 임무가 뜨면 로봇이 물을 계속
받으러 갑니다.
"""

import pytest

from potner_base.plant_conversions import (
    battery_percent,
    battery_wiring_suspect,
    lux_from_raw,
    moisture_percent,
    to_signed16,
)


def test_조도_변환은_데이터시트_계수를_쓴다():
    assert lux_from_raw(0) == pytest.approx(0.0)
    assert lux_from_raw(1200) == pytest.approx(1000.0)


def test_마른_상태는_0퍼센트_젖은_상태는_100퍼센트():
    """정전식 센서는 마를수록 값이 커집니다."""
    assert moisture_percent(20000, raw_dry=20000, raw_wet=9000) == pytest.approx(0.0)
    assert moisture_percent(9000, raw_dry=20000, raw_wet=9000) == pytest.approx(100.0)


def test_중간값은_비례한다():
    middle = moisture_percent(14500, raw_dry=20000, raw_wet=9000)
    assert middle == pytest.approx(50.0)


def test_기준을_벗어난_값은_0과_100으로_묶는다():
    """센서가 기준보다 더 마르거나 젖으면 음수나 100 초과가 나옵니다."""
    assert moisture_percent(25000, raw_dry=20000, raw_wet=9000) == 0.0
    assert moisture_percent(5000, raw_dry=20000, raw_wet=9000) == 100.0


def test_보정이_안_되면_None_을_돌려준다():
    """두 기준값이 같으면 계산이 불가능합니다. 노드는 이때 발행을 건너뜁니다."""
    assert moisture_percent(12345, raw_dry=0, raw_wet=0) is None


def test_배터리_만충과_방전():
    assert battery_percent(4.2 * 4, cell_count=4) == pytest.approx(100.0)
    assert battery_percent(3.0 * 4, cell_count=4) == pytest.approx(0.0)


def test_만충_이상과_과방전은_묶는다():
    assert battery_percent(20.0, cell_count=4) == 100.0
    assert battery_percent(5.0, cell_count=4) == 0.0


def test_셀_수에_따라_같은_전압이_다르게_해석된다():
    """모터팩 3S 와 젯슨팩 4S 를 같은 함수로 다룹니다."""
    voltage = 12.0
    assert battery_percent(voltage, cell_count=3) > battery_percent(
        voltage, cell_count=4
    )


def test_리튬이온_곡선을_쓴다():
    """단순 선형 환산이면 중간 구간이 통째로 어긋납니다.

    3.79V/셀 은 곡선상 50% 인데, 3.0~4.2V 를 선형으로 펴면 약 66% 가
    나옵니다. 곡선을 쓰는지 이 차이로 확인합니다.
    """
    curve = battery_percent(3.79 * 4, cell_count=4)
    linear = (3.79 - 3.0) / (4.2 - 3.0) * 100.0

    assert curve == pytest.approx(50.0)
    assert abs(curve - linear) > 10.0


def test_잔량은_전압에_대해_단조증가한다():
    previous = -1.0
    for millivolt in range(12000, 16900, 100):
        percent = battery_percent(millivolt / 1000.0, cell_count=4)
        assert percent >= previous
        previous = percent


def test_셀_수가_0이면_None():
    assert battery_percent(14.8, cell_count=0) is None


def test_정상_전압은_배선_의심_대상이_아니다():
    """4S 팩이 완전히 방전돼도 BMS 컷오프(약 12V) 부근이라 의심하지 않습니다."""
    assert battery_wiring_suspect(12.0, cell_count=4) is False
    assert battery_wiring_suspect(16.8, cell_count=4) is False


def test_VBS_배선이_빠지면_배선_의심으로_잡는다():
    """배선이 빠지면 0V 에 가까운 값을 읽습니다. battery_percent() 는 이걸
    그냥 0% 로 잘라버려 방전과 구분이 안 되므로, 전압 자체로 걸러냅니다."""
    assert battery_wiring_suspect(0.0, cell_count=4) is True
    assert battery_wiring_suspect(1.5, cell_count=4) is True


def test_셀_수마다_기준이_다르다():
    """3S 모터팩과 4S 젯슨팩은 정상 전압 범위가 다릅니다."""
    assert battery_wiring_suspect(7.0, cell_count=3) is False  # 3S 정상 범위
    assert battery_wiring_suspect(7.0, cell_count=4) is True   # 4S 라면 너무 낮음


def test_셀_수가_0이면_판단하지_않는다():
    assert battery_wiring_suspect(0.0, cell_count=0) is False


def test_16비트_부호_변환():
    """ADS1115 는 부호 있는 16비트를 돌려줍니다. 그냥 읽으면 음수가 6만이 됩니다."""
    assert to_signed16(0x0000) == 0
    assert to_signed16(0x7FFF) == 32767
    assert to_signed16(0x8000) == -32768
    assert to_signed16(0xFFFF) == -1
