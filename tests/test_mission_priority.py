"""임무 우선순위 판단 검증 (legacy/main.py 스케줄러 이식분)."""

from potner_mission.priority import Readings, StationMarker, Thresholds, evaluate


def test_아무_문제_없으면_할_일이_없다():
    readings = Readings(battery=100.0, moisture=80.0, light=500.0, temperature=22.0)
    assert evaluate(readings) is None


def test_배터리가_가장_우선이다():
    """전부 기준을 넘겨도 배터리부터. 꺼지면 아무것도 못 합니다."""
    readings = Readings(battery=10.0, moisture=5.0, light=10.0, temperature=40.0)
    assert evaluate(readings) is StationMarker.CHARGING


def test_수분이_두번째다():
    readings = Readings(battery=90.0, moisture=10.0, light=10.0, temperature=40.0)
    assert evaluate(readings) is StationMarker.WATER


def test_조도가_세번째다():
    readings = Readings(battery=90.0, moisture=80.0, light=50.0, temperature=40.0)
    assert evaluate(readings) is StationMarker.SUNLIGHT


def test_온도는_높을_때_발동한다():
    """다른 항목과 반대로 기준값 이상일 때 임무가 생깁니다."""
    readings = Readings(battery=90.0, moisture=80.0, light=500.0, temperature=33.0)
    assert evaluate(readings) is StationMarker.WIND

    readings.temperature = 25.0
    assert evaluate(readings) is None


def test_값을_모르는_센서는_건너뛴다():
    """센서가 아직 값을 안 보냈다고 해서 엉뚱한 임무를 만들면 안 됩니다."""
    readings = Readings(moisture=10.0)  # 나머지는 None
    assert evaluate(readings) is StationMarker.WATER

    assert evaluate(Readings()) is None


def test_임계값을_바꿀_수_있다():
    readings = Readings(battery=25.0)
    assert evaluate(readings) is None
    assert evaluate(readings, Thresholds(battery_percent=30.0)) is StationMarker.CHARGING


def test_마커_번호가_스테이션과_맞는다():
    """src/potner_docking/markers/ 의 파일명과 일치해야 합니다."""
    assert StationMarker.CHARGING == 1
    assert StationMarker.WATER == 2
    assert StationMarker.SUNLIGHT == 3
    assert StationMarker.WIND == 4
