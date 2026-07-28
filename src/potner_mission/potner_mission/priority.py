"""임무 우선순위 판단.

legacy/main.py 의 스케줄링 로직을 그대로 옮긴 순수 파이썬 모듈입니다.
ROS에 의존하지 않으므로 CI에서 단위 테스트로 검증됩니다.
"""

from dataclasses import dataclass
from enum import IntEnum


class StationMarker(IntEnum):
    """스테이션별 ArUco 마커 ID. src/potner_docking/markers/ 의 이미지와 일치합니다."""

    CHARGING = 1
    WATER = 2
    SUNLIGHT = 3
    WIND = 4


@dataclass
class Thresholds:
    """임무 발동 기준값. potner_bringup/config/potner_params.yaml 에서 주입됩니다."""

    battery_percent: float = 20.0
    moisture_percent: float = 30.0
    light_lux: float = 200.0
    temperature_celsius: float = 30.0


@dataclass
class Readings:
    """현재 센서 값. 값을 모르면 None으로 두면 해당 항목은 건너뜁니다."""

    battery: float = None
    moisture: float = None
    light: float = None
    temperature: float = None


def evaluate(readings, thresholds=None):
    """가장 시급한 임무 하나를 돌려줍니다. 할 일이 없으면 None.

    우선순위는 배터리 > 수분 > 일조량 > 온도 순서입니다. 배터리가 먼저인
    이유는 명확합니다 — 로봇이 꺼지면 나머지를 아무것도 못 합니다.
    """
    thresholds = thresholds or Thresholds()

    checks = (
        (readings.battery, thresholds.battery_percent, True, StationMarker.CHARGING),
        (readings.moisture, thresholds.moisture_percent, True, StationMarker.WATER),
        (readings.light, thresholds.light_lux, True, StationMarker.SUNLIGHT),
        (
            readings.temperature,
            thresholds.temperature_celsius,
            False,
            StationMarker.WIND,
        ),
    )

    for value, limit, trigger_when_below, station in checks:
        if value is None:
            continue
        breached = value < limit if trigger_when_below else value >= limit
        if breached:
            return station

    return None
