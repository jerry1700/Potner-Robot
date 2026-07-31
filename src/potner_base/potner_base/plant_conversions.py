"""센서 원시값을 사람이 쓰는 단위로 바꾸는 계산.

ROS와 I2C에 의존하지 않는 순수 파이썬 모듈입니다. 센서가 없어도 CI에서
단위 테스트로 검증됩니다.
"""

# BH1750 H-resolution 모드의 변환 계수. 데이터시트 고정값입니다.
BH1750_LUX_DIVISOR = 1.2

# 리튬이온 셀 하나의 개방회로 전압 대비 잔량 (V, %).
# 선형 보간으로 씁니다. 리튬이온은 3.7V 부근이 평평해서 단순 선형 환산을
# 하면 중간 구간이 통째로 어긋납니다.
LI_ION_CURVE = (
    (4.20, 100.0),
    (4.10, 90.0),
    (4.00, 80.0),
    (3.92, 70.0),
    (3.85, 60.0),
    (3.79, 50.0),
    (3.73, 40.0),
    (3.68, 30.0),
    (3.62, 20.0),
    (3.50, 10.0),
    (3.00, 0.0),
)


def lux_from_raw(raw: int) -> float:
    """BH1750 원시값을 lux 로 바꿉니다."""
    return raw / BH1750_LUX_DIVISOR


def moisture_percent(raw: int, raw_dry: int, raw_wet: int):
    """정전식 토양 수분 센서의 ADC 원시값을 % 로 바꿉니다.

    정전식 센서는 마를수록 값이 커집니다. 그래서 raw_dry 가 raw_wet 보다
    큽니다. 두 기준값은 실측해야 합니다.

        공기 중에 두고 읽은 값  -> raw_dry  (0%)
        물에 담그고 읽은 값     -> raw_wet  (100%)

    Args:
        raw: 지금 읽은 ADC 값
        raw_dry: 완전히 마른 상태의 ADC 값
        raw_wet: 완전히 젖은 상태의 ADC 값

    Returns:
        0~100 사이의 %. 두 기준값이 같아 보정이 안 된 상태면 None
    """
    span = raw_dry - raw_wet
    if span == 0:
        return None

    percent = (raw_dry - raw) / span * 100.0
    return max(0.0, min(100.0, percent))


def battery_percent(voltage: float, cell_count: int):
    """배터리 팩 전압을 잔량 % 로 바꿉니다.

    주행 중에는 부하 때문에 전압이 처져서 실제보다 낮게 나옵니다. 충전
    임무를 띄우는 기준으로는 이 정도로 충분하지만, 정지 상태에서 읽은
    값이 더 정확합니다.

    Args:
        voltage: 팩 전체 전압 (V)
        cell_count: 직렬 셀 수. 모터팩 3, 젯슨팩 4

    Returns:
        0~100 사이의 %. cell_count 가 0 이하면 None
    """
    if cell_count <= 0:
        return None

    per_cell = voltage / cell_count

    if per_cell >= LI_ION_CURVE[0][0]:
        return 100.0
    if per_cell <= LI_ION_CURVE[-1][0]:
        return 0.0

    for (high_v, high_pct), (low_v, low_pct) in zip(
        LI_ION_CURVE, LI_ION_CURVE[1:]
    ):
        if low_v <= per_cell <= high_v:
            ratio = (per_cell - low_v) / (high_v - low_v)
            return low_pct + ratio * (high_pct - low_pct)

    return None


# BMS 컷오프(약 3.0V/셀)보다 한참 아래인 전압입니다. 팩이 정상이라면
# 방전이 아무리 심해도 BMS 가 먼저 끊어서 이 아래로는 안 내려갑니다.
BATTERY_WIRING_FLOOR_PER_CELL = 2.0


def battery_wiring_suspect(voltage: float, cell_count: int) -> bool:
    """전압이 배선 문제를 의심할 만큼 낮은지 봅니다.

    battery_percent() 는 리튬이온 곡선 양 끝에서 값을 잘라 항상 0~100 을
    돌려주므로, VBS 배선이 빠져 0V 를 읽어도 그냥 0% 로 보입니다. 그러면
    앱에는 "방전"으로 보이고 배선 문제인 줄 모릅니다.

    구분하려면 잔량이 아니라 전압 자체를 봐야 합니다. 팩이 살아 있으면
    BMS 가 과방전을 막아서 셀당 약 3.0V 아래로는 내려가지 않으므로, 그보다
    훨씬 낮은 전압은 배선 문제로 봅니다.

    Args:
        voltage: 팩 전체 전압 (V)
        cell_count: 직렬 셀 수

    Returns:
        cell_count 가 0 이하면 False (판단 불가)
    """
    if cell_count <= 0:
        return False
    return voltage < BATTERY_WIRING_FLOOR_PER_CELL * cell_count


def to_signed16(raw: int) -> int:
    """ADS1115 등이 돌려주는 2바이트를 부호 있는 정수로 봅니다."""
    return raw - 0x10000 if raw & 0x8000 else raw
