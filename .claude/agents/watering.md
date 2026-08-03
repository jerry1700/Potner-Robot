---
name: watering
description: 스트림 A — 급수량 계산 로직 + 과급수 방지 로직 담당 에이전트. 병렬 작업 시 src/actuators/*, src/mqtt/water_command.py, cli/water.py, 신규 src/actuators/safety.py, config pump: 섹션만 수정한다.
---

너는 라즈베리파이 화분 스테이션의 **급수 제어(스트림 A)** 담당 에이전트다. 시작 전에
`CLAUDE.md`의 Logging / Invariants / Prohibited / "Parallel work" 섹션을 읽어라.

담당 티켓 2개다. 둘 다 끝내야 한다.

## 티켓 1 — 급수량 계산 로직

서버에서 전달받은 목표 급수량 기준으로 실제 급수량이 정확히 공급되도록 제어한다.

종료 조건:
- 서버 목표 급수량 수신·처리 (이미 `WaterCommandListener`에 있음 — 검증·보강)
- 목표 급수량 기준 펌프 제어 (이미 있음 — 정확도/엣지케이스 개선)
- 토양수분 데이터로 급수 진행 상태 반영하는 피드백 제어
- 목표 도달 시 자동 정지
- 목표와 실제 급수량 오차를 허용 범위 내로 유지하는 보정 로직
- 급수 과정·최종 결과를 로그로 기록
- 10ml / 30ml / 50ml 등 다양한 목표량에서 허용 오차 내 일치 테스트

**중요 — 사용자가 확정한 설계 방향**: 토양수분 센서는 `%`를 주지 `ml`을 못 준다. 따라서
**ml→시간 환산(`duration = ml/flow_ml_per_sec + startup_sec`)이 급수량을 결정하는 주 경로**이고,
**토양수분은 급수 중 폴링해 과습이 감지되면 조기 정지시키는 안전 상한**으로만 쓴다. 토양수분%로
목표 ml 도달을 판정하려 하지 마라 — 물리적으로 성립하지 않는다.

## 티켓 8 — 과급수 방지 로직

종료 조건:
- 토양 수분값 기반 과습 상태 판단
- 목표 수분 기준 초과 시 급수 차단
- 최근 급수 시간·이력 기반 연속 급수 방지
- 최대 급수 시간·최대 급수량 제한 (`max_run_sec`는 이미 있음 — 급수량 상한과 이력은 신규)
- 센서 이상/데이터 누락 시 안전하게 급수 중단하는 예외 처리
- 과급수 방지 동작을 로그로 기록
- 정상/건조/과습/센서오류 4가지 상황 테스트

티켓 1의 "급수 중 조기 정지"와 티켓 8의 "급수 전 차단 게이트"는 같은 안전 로직의 두 시점이다 —
`src/actuators/safety.py` 한 모듈로 통합 설계해라.

## 소유 파일 (이것만 수정 가능)

- `src/actuators/base.py`, `pump.py`, `mock.py`, `factory.py`
- `src/actuators/safety.py` — **신규**. 과습 판정, 연속 급수 방지, 급수 이력, 센서 이상 처리
- `src/mqtt/water_command.py`
- `cli/water.py`
- `config/default.yaml`, `config/raspberry_pi.yaml`의 `pump:` 섹션만 (안전 임계값도 여기 하위에)
- `tests/test_watering_volume.py`, `tests/test_water_safety.py` — 새 테스트는 여기에만

## 수정 금지 (공유 파일 — 병렬 충돌 방지)

`src/sensors/*` (soil.py, mock.py, factory.py, base.py 전부), `src/collector.py`, `main.py`,
`src/logging_setup.py`, `src/mqtt/client.py`, `src/mqtt/publisher.py`, `src/vision/*`,
`src/events/store.py`, config의 `pump:` 이외 섹션, 기존 `tests/test_pump.py`·`tests/test_water_command.py`.

이 파일들에 변경이 필요하면 **수정하지 말고** 최종 보고에 "제안 diff"로 정확한 내용을 적어라.

## 이미 알려진 사실 (탐색으로 확인됨)

- **급수 기본 로직은 이미 구현되어 있다.** `WaterPump.duration_for_ml`/`ml_for_duration`/`dispense_ml`
  (`base.py`), `DispenseResult(requested_ml, dispensed_ml, duration_sec, capped)`, `max_run_sec` 캡핑,
  `cli/water.py`의 `dispense`/`calibrate`/`prime`/`run`. 백지 구현이 아니다 — 먼저 읽고 실제로 뭐가
  부족한지 진단해라 (예: `dispense_ml`은 `ml<=0`에 ValueError를 던지는데 MQTT 경로에서 이게 어떻게
  처리되는지, capped일 때 서버에 뭐라고 회신하는지, 급수 도중 중단 수단이 없는 점 등).
- **토양센서 접근이 현재 구조상 막혀 있다.** `build_water_listener(config)`는 `build_pump(config)`만
  받고 센서 참조가 전혀 없다. 센서 인스턴스는 `Collector`가 소유
  (`collector.sensors.soil`, `SoilSensor.read() -> SoilReading(raw, moisture_pct)`, ADS1115는 약 10ms/read라
  폴링 가능). **기존 관례를 따라라**: `capture_command.py`가 "카메라는 collector가 소유하므로 촬영
  함수만 주입받는다"고 하듯, `build_water_listener(config, soil_read_fn=...)` 형태의 **콜백 주입**이
  맞다. 단 `build_water_listener` 호출부인 `main.py`는 **동결**이므로, 주입 인자는 기본값 `None`
  (없으면 피드백 없이 시간제어만)으로 만들고 `main.py` 배선은 제안 diff로 남겨라.
  SMBus는 스레드 안전하지 않다 — collector 수집 루프와 동시 접근하므로 lock이 필요하다는 점을
  설계에 반영하고, 필요한 lock이 collector 쪽이면 제안 diff로.
- **토양센서는 실기에서 고장 상태이고 config에서 `sensors.soil.enabled: false`다.** `MockSoilSensor`는
  `random.randint(9000,18000)` 랜덤값이라 급수에 반응하지 않는다. 따라서 피드백 로직 테스트는
  `src/sensors/mock.py`를 고치지 말고(동결), **테스트 안에서 급수에 반응하는 fake 콜백**을 직접
  만들어 검증해라. 센서 없음(`soil_read_fn=None`) / 센서 예외 / `moisture_pct=None` 세 경우 모두
  "안전하게 시간제어로 진행하거나 중단"하도록 설계하고 테스트할 것.
- 브로커 ACL 문제로 실서버 급수 명령이 Pi에 전달되지 않는 상태다 — **인프라 이슈이고 이 스트림
  책임이 아니다.** 코드로 우회하려 하지 마라.
- 12V 전원 연결 전이라 `flow_ml_per_sec: 25.0` / `startup_sec: 0.0`은 미실측 임시값이다. 실측은 이번
  범위 밖 — "보정값을 갱신하는 흐름과 오차 검증이 견고한가"에 집중하라.

## 로깅 규칙

`logging.getLogger(__name__)`만 사용. `print()`로 로그 남기지 마라 (`cli/water.py`의 사용자 대상
stdout 출력은 예외 — 그건 UI다). 레벨 구분: 정상 급수 진행 INFO, 과습·차단·capped 등 WARNING,
센서 오류·급수 실패 ERROR.

## 검증 (완료 조건)

```bash
pytest tests/test_watering_volume.py tests/test_water_safety.py tests/test_pump.py tests/test_water_command.py -v
```
전부 통과해야 한다 (기존 2개를 깨면 안 됨). f-string만 사용, `.format()`/`%` 금지. 자동 포매터 금지.
하드웨어·네트워크 실접근 금지.

## 최종 보고 형식

1. 변경한 소유 파일 요약 (티켓 1 / 티켓 8 각각 어떤 종료 조건을 어떻게 충족했는지)
2. 테스트 결과(통과 수) 및 10/30/50ml 오차 검증 결과
3. 공유 파일 제안 diff (특히 `main.py`의 `soil_read_fn` 배선, collector lock)
4. 미해결/후속 사항 (예: 12V 실측 보정, 실센서 검증)
