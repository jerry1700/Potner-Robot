# MQTT 메시지 명세

로봇(Jetson) · 스테이션(Raspberry Pi) · 서버(Spring Boot) 가 주고받는
메시지 형식입니다.

**한쪽만 바꾸면 아무 에러 없이 값이 안 들어옵니다.** 서버가 모르는
`sensorType` 을 받으면 조용히 버리고, 로봇은 잘 보냈다고 생각합니다.
그래서 이 문서를 단일 기준으로 둡니다. 바꿀 때는 세 파트가 함께 바꿉니다.

관련 코드
- 로봇: [`src/potner_bridge/potner_bridge/telemetry.py`](../src/potner_bridge/potner_bridge/telemetry.py)
- 검증: [`tests/test_telemetry.py`](../tests/test_telemetry.py) — 이 문서와 코드가 어긋나면 CI 가 잡습니다

## 공통 규칙

| 항목 | 규칙 |
|---|---|
| 인코딩 | UTF-8, 한글 이스케이프하지 않음 |
| `messageId` | UUID v4 문자열 (36자) |
| `deviceId` | 기기 식별자. 토픽 경로와 같은 값 |
| 시각 | **ISO 8601 UTC, 초 단위, `Z` 로 끝남** — `2026-07-23T08:00:00Z` |

시각을 UTC 로 고정하는 이유는, 로봇과 서버의 시간대가 다르면 일기 생성과
성장 기록의 순서가 뒤섞이기 때문입니다. 한국 시각을 그대로 보내면 9시간
어긋난 기록이 쌓입니다.

## 기기 식별자

| deviceId | 기기 |
|---|---|
| `jetson-01` | 로봇 (Jetson Orin Nano) |
| `raspberry-01` | 장치 스테이션 (Raspberry Pi 5) |

---

## 1. 센서 측정값

**측정값 하나당 메시지 하나**입니다. 여러 센서를 한 메시지에 묶지 않습니다.

```
발행: potner/device/{deviceId}/sensor/telemetry
```

```json
{
  "messageId": "3f2b1c8e-5a4d-4e7f-9c1a-2b3d4e5f6a7b",
  "deviceId": "jetson-01",
  "sensorType": "SOIL_MOISTURE",
  "value": 42.5,
  "unit": "PERCENT",
  "measuredAt": "2026-07-23T08:00:00Z"
}
```

`measuredAt` 은 **발행 시각이 아니라 실제로 센서를 읽은 시각**입니다.
전송 주기가 10초라 발행 시각을 쓰면 최대 10초 어긋납니다.

### SensorType

```java
package com.potner.sensor.domain;

public enum SensorType {
    TEMPERATURE,
    HUMIDITY,
    SOIL_MOISTURE,
    ILLUMINANCE,
    BATTERY            // 추가 요청 — 4S 젯슨팩 잔량
}
```

### SensorUnit

```java
package com.potner.sensor.domain;

public enum SensorUnit {
    CELSIUS,
    PERCENT,
    LUX
}
```

`BATTERY` 는 기존 `PERCENT` 를 그대로 쓰므로 **SensorUnit 은 바꿀 필요가
없습니다.**

### 종류별 단위와 발행 주체

| sensorType | unit | 발행 | 출처 |
|---|---|---|---|
| `SOIL_MOISTURE` | `PERCENT` | `jetson-01` | 정전식 센서 → ADS1115 |
| `ILLUMINANCE` | `LUX` | `jetson-01` | BH1750 |
| `BATTERY` | `PERCENT` | `jetson-01` | INA226 전압 → 리튬이온 곡선 환산 |
| `TEMPERATURE` | `CELSIUS` | `raspberry-01` | 스테이션 온습도 센서 |
| `HUMIDITY` | `PERCENT` | `raspberry-01` | 스테이션 온습도 센서 |

로봇은 대기 온도를 직접 재지 않고 스테이션이 올린 값을 구독해서 씁니다.
젯슨에서 DHT11 을 읽으려면 마이크로초 타이밍이 필요한데 리눅스는 실시간
OS 가 아니라 자주 실패합니다.

**배터리 값은 전압에서 환산한 추정치입니다.** 주행 중에는 부하 때문에
전압이 처져 실제보다 낮게 나옵니다. 충전 시점 판단에는 충분하지만 정밀한
잔량계로 쓰지 마세요.

### 배터리를 한 종류만 재는 이유

로봇에는 팩이 두 개입니다 — 4S 젯슨팩과 3S 모터팩. 그중 **젯슨팩만**
측정합니다.

젯슨은 15~25W 를 계속 먹는 반면 모터는 이동할 때만 돕니다. 용량도 젯슨팩
51.8Wh, 모터팩 38.9Wh 로 비슷해서, 실사용에서는 **젯슨팩이 먼저 바닥납니다.**
먼저 떨어지는 쪽을 재면 충전 임무가 제때 걸리므로 모터팩까지 계측할 필요가
없습니다. INA226 도 한 개뿐입니다.

대신 **모터팩 고갈은 감지되지 않습니다.** 예상과 달리 모터팩이 먼저 떨어지면
로봇이 이유 없이 멈춘 것처럼 보입니다. 실주행에서 두 팩의 소모 속도를 한 번
확인해두면 좋습니다.

---

## 2. 하트비트

기기가 살아 있다는 신호입니다.

```
발행: potner/device/{deviceId}/status/heartbeat
주기: 30초
```

```json
{
  "messageId": "8c7d6e5f-4a3b-2c1d-9e8f-7a6b5c4d3e2f",
  "deviceId": "jetson-01",
  "sentAt": "2026-07-23T08:00:00Z"
}
```

---

## 3. 로봇 상태

앱에서 "로봇이 지금 무엇을 하는지" 보여주기 위한 것입니다.
**상태가 바뀔 때마다** 발행하고, 하트비트 주기마다 한 번 더 보냅니다.
MQTT 메시지가 유실되어도 30초 안에 복구되도록 하기 위함입니다.

```
발행: potner/device/{deviceId}/status/state
```

```json
{
  "messageId": "1a2b3c4d-5e6f-7a8b-9c0d-1e2f3a4b5c6d",
  "deviceId": "jetson-01",
  "state": "DOCKING",
  "changedAt": "2026-07-23T08:00:00Z"
}
```

### RobotState

```java
package com.potner.robot.domain;

public enum RobotState {
    IDLE,          // 대기. 센서를 지켜보는 중
    NAVIGATING,    // 스테이션으로 이동 중 (Nav2)
    DOCKING,       // 마커를 보며 정밀 접근 중
    SERVICING,     // 스테이션에서 급수·송풍 등을 받는 중
    GREETING       // 귀가한 사용자를 반기는 중
}
```

---

## 4. 서버 → 로봇: 대사 전달

서버 LLM 이 만든 문장을 로봇이 소리로 냅니다. 로봇에는 마이크가 없어서
음성 인식은 앱이 하고, 로봇은 말하기만 합니다.

```
구독: potner/device/{deviceId}/speech
```

payload 는 **JSON 이 아니라 평문 문자열**입니다.

```
다녀오셨어요? 오늘도 잘 지냈어요.
```

로봇은 300자까지만 말합니다. 넘으면 잘라서 읽습니다. LLM 이 긴 답을
보내면 로봇이 몇 분 동안 혼자 떠들게 되기 때문입니다.

---

## 5. 로봇 → 스테이션: 서비스 요청 (미확정)

도킹을 마친 뒤 스테이션에 급수나 송풍을 시작하라고 알립니다.

> ⚠️ **아직 확정되지 않았습니다.** `Raspberry-feature/mqtt-command-receiver/186`
> 담당자와 맞춘 뒤 이 절을 갱신하세요. 현재 로봇 코드는 아래 형태로
> 발행하고 있습니다.

```
발행: potner/station/{역할}/request
```

역할은 `water`, `wind` 를 씁니다. 충전과 일광욕은 스테이션이 할 일이
없어(접점 접촉과 위치 이동만) 요청을 보내지 않습니다.

---

## 6. 스테이션 → 로봇: 도킹 접점 확인 (미확정)

스테이션의 A3144 홀 센서가 로봇의 자석을 감지했다는 신호입니다. 카메라
정렬보다 확실한 물리적 접촉 증거라 도킹 성공 판정에 씁니다.

> ⚠️ **아직 확정되지 않았습니다.** 5절과 함께 맞추세요.

```
구독: potner/station/{stationId}/docked
```

payload 는 `1` / `true` 면 접점 확인입니다.

---

## 변경할 때

1. 이 문서를 먼저 고칩니다
2. `telemetry.py` 와 `tests/test_telemetry.py` 를 함께 고칩니다
3. 서버·스테이션 담당자에게 알립니다

테스트가 문서의 예시와 같은 값을 검사하므로, 코드만 바꾸고 문서를 안
고치면 리뷰에서 드러납니다.
