# 젯슨 ↔ 서버 연동 명세

기준 시점: 2026년 8월 3일. 서버는 `Server-master`(2ff54e5) 가 EC2 에 배포되어 있고
**서버 쪽 수정 없이** 이 문서대로 구현하면 붙는다.

> **문서 세 개의 관계**
> - [`DEVICE-MQTT.md`](DEVICE-MQTT.md) — 장치 공통 계약과 설계 근거. 두 장치가 함께 지켜야 할 규칙
> - [`DEVICE-RASPBERRY.md`](DEVICE-RASPBERRY.md) — 라즈베리가 할 일
> - **`DEVICE-JETSON.md`** (이 문서) — 젯슨이 할 일만 추린 것. 현재 동작 상태 포함
>
> 세 문서가 어긋나면 **코드가 기준**이다. 설계를 왜 그렇게 했는지는 `DEVICE-MQTT.md` 와
> `BACKEND.md`(서버 저장소에 있음 — 이 저장소에는 없다) 에 있다.

젯슨은 **움직이는 화분**에 실려 있다. 바퀴·디스플레이·토양수분 센서가 여기 달려 있고,
급수·촬영·송풍을 받으려면 스테이션(라즈베리파이가 있는 곳)으로 찾아가야 한다.

---

## 0. 지금 상태 요약

| 기능 | 구현 | 실제 동작 |
|---|---|---|
| 토양수분 전송 | ✅ | ✅ **10초마다 들어오고 있음** |
| 하트비트 | ✅ | 🟡 간헐적 — 08-03 01:59 이후 끊김 |
| 표정 수신 | ✅ `face_display_node` | ✅ 유일하게 완성된 수신 경로 |
| 귀가 마중 수신 | ✅ `arrival_contract.py` | 미검증 |
| **이동 수신 (`command/navigate`)** | 🔴 **없음** | 🔴 `mqtt_bridge_node` 가 **"처리하지 않는 명령"** 로그만 남기고 무시 |
| 로봇 행동 상태 발행 | ✅ 코드 있음 | 🔴 서버에 데이터 없음 (`current_state` 갱신 안 됨) |
| 배터리 발행 | ✅ 코드 있음 | 🔴 서버에 데이터 없음 (`battery_percent` NULL) |

### 🔴 가장 중요한 공백 — `command/navigate`

`mqtt_bridge_node.py` 가 `command/#` 를 구독하지만 처리하는 것은 두 가지뿐이다:

```python
# welcome_start / welcome_cancel  → arrival
# command/expression              → 표정
else:
    self.get_logger().info(f"처리하지 않는 명령: {message.topic}", ...)
```

`command/navigate` 가 오면 **로그만 찍고 아무것도 하지 않는다.** 서버는 120초를 기다리다
`TIMED_OUT` 으로 끊는다.

> 08-03 01:53·01:57 에 `result/navigate OK` 가 들어와 자동 급수·촬영 체인이 완주했는데,
> 그건 사람이 `mosquitto_pub` 으로 수동 발행한 것이다. 젯슨 코드가 보낸 것이 아니다.

**이동 명령 5개 체인이 전부 이 수신을 기다린다** — 자동 급수·자동 말리기·자동 촬영·일광
이동·귀가 마중.

---

## 1. 브로커 접속

| 항목 | 값 |
|---|---|
| 호스트 | `i15e104.p.ssafy.io` |
| 포트 | **1884** (도커 내부는 1883) |
| 계정 | `jetson-01` — **`device_uid` 와 같아야 한다** |
| 비밀번호 | 팀 공유. `password.txt` 는 git 에 없다 |
| QoS | 1 |
| TLS | 없음 (평문) |

### client_id 는 유일해야 한다

서버가 `potner-backend-prod` 와 `potner-backend-prod-command` 두 개를 쓴다. 겹치면 브로커가
기존 세션을 끊어 서로를 계속 밀어낸다.

```python
client_id = f"{device_uid}-bridge"   # 예: jetson-01-bridge
```

### 재접속마다 다시 구독해야 한다

`clean_session=True` 면 구독이 남지 않는다. `on_connect` 에서 매번 `subscribe` 한다.

### ACL

```
pattern write potner/device/%u/sensor/#
pattern write potner/device/%u/status/#
pattern write potner/device/%u/result/#
pattern read  potner/device/%u/command/#
```

🔴 **`command/` 아래에 쓰면 조용히 버려진다.** 결과는 반드시 `result/` 아래로.

---

## 2. `device_uid` — 네 곳이 같아야 한다

| # | 위치 | 대조 방식 |
|---|---|---|
| 1 | MQTT 토픽 세그먼트 | 정규식 |
| 2 | 페이로드 `deviceId` | 1번과 `String.equals` — **대소문자 구분** |
| 3 | DB `iot_device.device_uid` | 대소문자 무시 |
| 4 | mosquitto 계정명 | **대소문자 구분** |

**코드에서 상수 하나를 토픽 조립과 페이로드에 함께 쓴다.**

현재 `jetson-01` 은 `robot-01`(dudu 로봇) 아래에 등록되어 `dudu` 식물에 배정되어 있다.

---

## 3. 시각 규약

오프셋 포함 ISO-8601, `Z` 권장. 🔴 **현재보다 10분 이상 미래면 버려진다.** NTP 필수.

---

## 4. 토양수분 전송 — 젯슨이 보낸다

센서가 화분 흙에 있으므로 **젯슨이 보내는 것이 맞다.** 서버는 어느 종류의 장치가 어떤 센서를
보내는지 제한하지 않는다 — `iot_device.device_uid` → 로봇 → 활성 배정 순으로 식물을 정하므로,
파이의 온습도와 젯슨의 토양수분이 같은 식물로 모인다.

```
potner/device/jetson-01/sensor/telemetry
```

```json
{
  "messageId": "3f2b1c8a-5d4e-4f6a-9b0c-1d2e3f4a5b6c",
  "deviceId": "jetson-01",
  "sensorType": "SOIL_MOISTURE",
  "value": 32.5,
  "unit": "PERCENT",
  "measuredAt": "2026-08-03T02:15:00Z"
}
```

| `sensorType` | `unit` | 범위 |
|---|---|---|
| `SOIL_MOISTURE` | `PERCENT` | 0 ~ 100 |

enum 은 **대소문자까지 정확히** 같아야 한다.

### 🔴 `messageId` 는 측정값마다 새 UUID

`sensor_reading.device_message_id` 가 전역 UNIQUE 다. 재사용하면 조용히 버려지고 로그에도
안 남는다(DEBUG 레벨).

### 수집 주기는 5분보다 짧게

이상 알림이 뜨려면 "최근 15분 안에 3건" 이 필요하다. 현재 10초 주기라 문제없다.

### 자동 급수의 방아쇠다

토양수분 3건의 **중앙값**이 기준 미만이면 알림이 생기고 자동 급수 체인이 시작된다.
현재 `dudu` 기준은 40~55% 다.

- 단발 튐값은 중앙값이 걸러낸다 (`45 / 44 / 2` → 중앙값 44, 알림 없음)
- 한 번 뜬 알림은 **41.5% 이상**(= `40 + (55−40)×0.1`)으로 복귀해야 닫힌다
- 알림이 열려 있는 동안은 체인이 다시 시작되지 않는다

> 반복 시연을 하려면 급수 후 45~50% 를 보내 알림을 닫고, 다시 40 미만으로 내려야 한다.

### 조도 센서도 젯슨으로 옮기는 것을 검토

지금 조도가 스테이션(파이)에 있다. 화분을 햇빛 자리로 보내도 측정값이 안 올라서, 서버의
자동 일광 이동이 목표를 채우지 못하고 창(8~17시)이 닫힐 때까지 화분이 햇빛 자리에 머문다.
`bh1750` 은 I2C 라 젯슨에도 붙는다. **옮기면 서버 수정 없이 일광 이동이 의미대로 동작한다.**

---

## 5. 하트비트

```
potner/device/jetson-01/status/heartbeat
{"messageId":"<uuid>","deviceId":"jetson-01","sentAt":"2026-08-03T02:15:00Z"}
```

**30초 주기 권장.** 90초간 없으면 `OFFLINE` 으로 표시된다.

7·8절의 신호(상태·배터리)도 생존 증거로 쳐서 `last_seen_at` 을 갱신하지만, 명시적으로 보내는
편이 진단에 좋다.

현재 간헐적으로만 들어온다. 앱의 장치 관리에서 `OFFLINE` 으로 보이는 원인이다.

---

## 6. 이동 — `command/navigate` 수신 → `result/navigate` 회신 (🔴 미구현, 최우선)

### 받는 것

```
potner/device/jetson-01/command/navigate
```

```json
{
  "destination": "WATER_STATION",
  "x": 1.250,
  "y": -0.480,
  "yaw": 1.5708,
  "requestId": "968a1495-a3ec-43b9-8204-ef05a1ecbed5"
}
```

| 필드 | 설명 |
|---|---|
| `destination` | `WATER_STATION` / `HOME` / `SUNLIGHT` / `GREETING`. **로그와 상태 보고용 이름** |
| `x` `y` `yaw` | map 프레임 좌표 (m, rad) |
| `requestId` | 회신에 그대로 반향 |

### 🔴 좌표를 그대로 쓴다

출처는 **서버의 위치 저장소**다. 로봇 파라미터의 `station_poses` 는 쓰지 않는다 — 좌표의
출처가 둘이 되면 어긋난다. 지도를 다시 그리면 앱에서 좌표를 다시 넣어야 하고, 그러면 서버
값만 바꾸면 로봇이 따라온다.

미설정 좌표는 `0,0,0` 이 아니라 **NULL** 이다. 서버가 NULL 인 목적지로는 명령을 발행하지
않으므로, 젯슨은 항상 유효한 좌표를 받는다.

### 보내는 것

```
potner/device/jetson-01/result/navigate
```

```json
{
  "messageId": "<uuid>",
  "deviceId": "jetson-01",
  "requestId": "<그대로 반향>",
  "status": "OK",
  "measuredAt": "2026-08-03T02:15:30Z"
}
```

| `status` | 뜻 |
|---|---|
| `OK` | **도착(도킹 완료).** 이 회신이 와야 서버가 다음 명령을 이어 보낸다 |
| `ERROR` | 실패. `error` 문구 (예: `DOCKING_FAILED`, `NAVIGATION_FAILED`) |
| `BUSY` | 이동 중 새 명령이 와서 거부 |

### 🔴 자율 임무를 꺼야 한다

**모든 동작 명령은 서버를 거친다**는 것이 팀 결정이다. 서버가 생육 데이터(수분·누적 광량)로
판단해 명령을 내리고, 로봇은 받은 명령만 수행한다.

`mission_manager` 의 자율 임무(임계값 판단 → 스스로 이동)를 켠 채로 서버 명령을 받으면
**두 주인의 명령 사이에서 왕복한다.**

### 이 수신이 막고 있는 것

| 체인 | 흐름 |
|---|---|
| 자동 급수 | `NAVIGATE(WATER_STATION)` → 파이 `WATER` → `NAVIGATE(HOME)` |
| 자동 말리기 | `NAVIGATE(WATER_STATION)` → 파이 `FAN` 반복 → `NAVIGATE(HOME)` |
| 자동 촬영 | `NAVIGATE(WATER_STATION)` → 파이 `CAPTURE` → `NAVIGATE(HOME)` |
| 일광 이동 | `NAVIGATE(SUNLIGHT)` → 목표 채우면 `NAVIGATE(HOME)` |

**넷 다 첫 단계가 이동이다.** 회신이 없으면 120초 뒤 `TIMED_OUT` 으로 끊기고 다음 단계로
넘어가지 않는다.

### 타임아웃

**120초.** 늦게 온 회신은 타임아웃 기록 위에 덮어쓴다.

---

## 7. 로봇 행동 상태

```
potner/device/jetson-01/status/state
```

```json
{
  "messageId": "1a2b3c4d-...",
  "deviceId": "jetson-01",
  "state": "NAVIGATING",
  "changedAt": "2026-08-03T02:15:00Z"
}
```

`state` **5종.** 이 외의 값은 JSON 파싱 단계에서 버려진다.

| 값 | 뜻 |
|---|---|
| `IDLE` | 대기 중 |
| `NAVIGATING` | 이동 중 |
| `DOCKING` | 마커 보며 정밀 접근 중 |
| `SERVICING` | 급수·송풍 받는 중 |
| `GREETING` | 사용자 반기는 중 |

- **같은 상태를 반복 발행해도 된다.** 서버 처리가 멱등하고, 실제로 바뀔 때만 "바뀐 시각" 을
  갱신하므로 반복 발행이 그 시각을 밀어내지 않는다
- `changedAt` 은 로그·진단용이다. 서버는 **수신 시각**을 저장한다 — 장치 시계 오차가 급수
  타임아웃 계산에 섞이지 않게 하려는 것이다

### 서버가 하는 일

1. 현재 상태를 저장해 앱 장치 관리 화면에 노출
2. **`SERVICING`·`GREETING` 이면 디스플레이 표정을 `VERY_HAPPY` 로 바꾼다**

2번이 중요하다. **급수 중에 표정이 저절로 매우행복이 되는 것**이 이 신호에 달려 있다.
지금 서버에 상태 데이터가 없어서 그 연출이 안 나온다.

성공해도 로그에 안 남는다(바뀔 때만 INFO). 확인은
`GET /api/v1/plants/{plantId}/devices` 의 `currentState` 로 한다.

---

## 8. 배터리 잔량 — 젯슨만

```
potner/device/jetson-01/status/battery
{"messageId":"<uuid>","deviceId":"jetson-01","batteryPercent":78,
 "measuredAt":"2026-08-03T02:15:00Z"}
```

🔴 **젯슨만 보낸다. 라즈베리가 보내면 서버가 거부한다.** `robot.battery_percent` 가 컬럼
하나라 두 장치가 보내면 서로 덮어써 값이 흔들리기 때문이다. 보고 주체는 서버 설정
`potner.device.battery-reporter-type = JETSON_ORIN` 이 정한다.

| 필드 | 규칙 |
|---|---|
| `batteryPercent` | **정수 0~100.** 범위를 벗어나면 **버린다** — 0/100 으로 깎지 않는다(센서 고장을 숨기지 않으려고) |
| | 실수를 보내면 내린다 (`78.9` → `78`). 범위 검사는 내린 뒤 값에 걸리므로 `101.2` 는 버려진다 |
| `messageId` | 중복 제거용이 아니다. 단일 컬럼 덮어쓰기라 같은 메시지가 두 번 와도 결과가 같다 |

**60초 주기 권장.** 배터리는 천천히 변한다.

같은 값이 다시 와도 "받은 시각" 을 갱신한다. 로봇 상태와 다른 점이다 — 상태는 "언제부터 이
상태인지" 가, 배터리는 "이 값이 얼마나 최근인지" 가 필요하다.

배터리 부족 알림은 아직 없다. 값만 앱에 노출된다.

---

## 9. 디스플레이 표정 — 서버 → 젯슨 (✅ 구현됨)

```
potner/device/jetson-01/command/expression
{"plantId":"...","expression":"HAPPY","reason":"SUNLIGHT"}
```

**젯슨이 실제로 구현해 둔 유일한 수신 경로다.** `mqtt_bridge_node` → `display/expression`
→ `face_display_node` → `face.py` 렌더.

| `expression` | 언제 |
|---|---|
| `VERY_HAPPY` | 급수·송풍 받는 중, 사용자 반기는 중 |
| `HAPPY` | 오늘 꽃이 폈거나, 충분한 광량을 받고 있음 |
| `NEUTRAL` | 특별한 일 없음 |
| `SAD` | 온도나 습도가 기준을 벗어남 |

`reason` 은 말풍선 문구를 고를 때 쓴다. 안 써도 된다.
`WATERING` `GREETING` `BLOOMED` `SUNLIGHT` `TEMPERATURE` `HUMIDITY` `NONE`

### 두 가지를 지켜야 한다

1. **30초마다 같은 값이 다시 온다.** 중복을 걸러낼 필요 없이 마지막 값을 그리면 된다.
   로봇이 재시작해도 한 주기 안에 표정을 되찾는다
2. **모르는 값이 올 수 있다.** 서버가 표정이나 이유를 늘릴 수 있으니 알 수 없는 값은 기본
   표정으로 떨어뜨린다. 그러면 서버가 값을 추가할 때 로봇을 고치지 않아도 된다

### 배정된 식물이 없으면 아무것도 오지 않는다

서버가 "활성 배정이 있는 식물의 젯슨" 에게만 보낸다.

### 앱에서 수동으로 보낼 수 있다

디버그 빌드의 장치 관리 화면에 표정 4종 버튼이 있다. 누르면 서버가 즉시 발행한다.
**단 다음 주기(30초)에 서버 판정이 덮어쓴다.** 로봇 디스플레이가 살아 있는지 확인하는 용도다.

---

## 10. 귀가 마중 — 서버 → 젯슨 (✅ 구현됨, 미검증)

GPS 전에는 앱의 임시 버튼이, 이후에는 Android Geofence 가 같은 API 를 호출한다.
`requestId` 는 `eventId` 와 같고 **QoS 1 중복 수신에도 같은 이동을 다시 시작하지 않아야 한다.**

### 마중 시작

```
potner/device/jetson-01/command/welcome_start
{"eventId":"<uuid>","visitId":"<uuid>","requestId":"<eventId와 같음>",
 "destination":"GREETING","x":1.25,"y":-0.48,"yaw":1.5708,
 "returnDestination":"HOME","returnX":0.10,"returnY":0.20,"returnYaw":0.0,
 "waitSeconds":120,"totalTimeoutSeconds":300,"publishedAt":"..."}
```

```
potner/device/jetson-01/result/welcome_start
{"messageId":"<새 uuid>","deviceId":"jetson-01","requestId":"<그대로>","status":"OK"}
```

`OK` 는 **GREETING 좌표 도착**을 뜻한다. 도착 뒤 사람을 인식하면 인사하고 HOME 으로 복귀한다.
사람이 안 보이면 `waitSeconds` 뒤 복귀하고, `totalTimeoutSeconds` 를 넘기면 기다리지 않고
복귀를 시작한다.

### 마중 취소

```
potner/device/jetson-01/command/welcome_cancel
{"eventId":"<새 uuid>","visitId":"<start와 같음>","requestId":"<eventId와 같음>",
 "returnDestination":"HOME","returnX":0.10,"returnY":0.20,"returnYaw":0.0,
 "publishedAt":"..."}
```

```
potner/device/jetson-01/result/welcome_cancel
{"messageId":"<새 uuid>","deviceId":"jetson-01","requestId":"<그대로>","status":"OK"}
```

취소 `OK` 는 **HOME 좌표 도착**을 뜻한다. 이동 실패는 `ERROR`, 다른 방문이나 임무 수행 중이라
시작하지 못하면 `BUSY` 다. 실패에는 `error` 와 기계 판독용 `code` 를 함께 보낼 수 있다.

### 상태 조회

서버가 `GET /api/v1/arrival/events/{eventId}` 로 처리 상태를 노출한다. 앱이 이걸 폴링한다.

---

## 11. 안 될 때 — 로그 문구로 원인 찾기

```bash
docker logs --since 5m potner-infra-test-backend-1 2>&1 \
  | grep -iE "sensor reading|heartbeat|robot state|battery|navigate|welcome|command result"
```

| 순서 | 검사 | 실패 시 로그 |
|---|---|---|
| 1 | 토픽 정규식 | `Invalid MQTT <신호> topic` |
| 2 | JSON·enum | `Invalid MQTT <신호> JSON` — **서버가 모르는 `state` 값 포함** |
| 3 | 필수값·범위 | `Invalid MQTT <신호> values: fields=...` — 배터리 0~100 초과 |
| 5 | 미래 시각 | `Invalid MQTT <신호> measuredAt` |
| 6 | 토픽 ↔ 페이로드 | `MQTT <신호> deviceId mismatch` |
| 7 | 저장 | `IoT device was not found` — **등록 안 됨** |
| 7 | 저장 | `device type is not the configured reporter` — **젯슨이 아닌 장치가 배터리를 보냄** |
| — | 미구독 토픽 | `topic is unsupported` |

### 성공 로그가 없는 신호

기본 로그 레벨이 INFO 라 **하트비트와 배터리는 성공해도 흔적이 없다.** 로봇 상태도 같은
상태가 반복되는 동안에는 남지 않는다(바뀔 때만 INFO).

그 신호들은 **결과를 봐야** 한다:

```
GET /api/v1/plants/{plantId}/devices
  robot.currentState        로봇 상태
  robot.stateChangedAt
  robot.batteryPercent      배터리
  robot.batteryMeasuredAt   ← 갱신 안 되면 배터리가 안 들어오는 것
  devices[].connectionStatus  하트비트
```

---

## 12. 남은 작업 정리

| 우선순위 | 작업 |
|---|---|
| 🔴 1 | **`command/navigate` 수신 + `result/navigate` 반향** — 이동 체인 4개가 전부 여기서 막힌다 |
| 🔴 2 | **`mission_manager` 자율 임무 끄기** — 서버 명령과 충돌한다 |
| 🔴 3 | **하트비트 안정화** — 지금 간헐적이라 앱에 OFFLINE 으로 보인다 |
| 🟡 4 | **`status/state` 발행** — 급수 중 표정이 `VERY_HAPPY` 로 바뀌는 연출이 여기 달려 있다 |
| 🟡 5 | **`status/battery` 발행** — 앱 장치 관리의 배터리 표시 |
| ⚪ 6 | 조도 센서를 젯슨으로 이설 검토 — 일광 이동이 의미대로 동작하게 된다 |
| ⚪ 7 | 귀가 마중 실기 검증 |

---

## 부록 — 참고 구현 골격

```python
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

import paho.mqtt.client as mqtt

DEVICE_UID = "jetson-01"

ROBOT_STATES = {"IDLE", "NAVIGATING", "DOCKING", "SERVICING", "GREETING"}
KNOWN_EXPRESSIONS = {"VERY_HAPPY", "HAPPY", "NEUTRAL", "SAD"}
DEFAULT_EXPRESSION = "NEUTRAL"


def _now() -> str:
    # 서버 시각보다 10분 이상 미래면 버려진다. NTP 동기화가 필요하다.
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


class JetsonBridge:
    def __init__(self, host: str, *, username: str, password: str, port: int = 1884):
        self.device_uid = username  # 계정명 == device_uid == 토픽 세그먼트
        self.qos = 1
        # 토픽과 페이로드 deviceId 를 같은 변수에서 만든다. 두 군데 따로 적으면
        # 대소문자가 어긋나 서버가 버린다.
        self._sensor_topic = f"potner/device/{self.device_uid}/sensor/telemetry"
        self._state_topic = f"potner/device/{self.device_uid}/status/state"
        self._battery_topic = f"potner/device/{self.device_uid}/status/battery"
        self._heartbeat_topic = f"potner/device/{self.device_uid}/status/heartbeat"
        self._command_topic = f"potner/device/{self.device_uid}/command/#"

        self._client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=f"{self.device_uid}-bridge",   # 서버와 겹치면 서로를 끊어낸다
        )
        self._client.username_pw_set(username, password)
        self._client.on_connect = self._on_connect
        self._client.on_message = self._on_message
        self._client.connect(host, port, keepalive=60)
        self._client.loop_start()

    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        # 재접속마다 다시 구독해야 한다. clean session 이면 구독이 남지 않는다.
        client.subscribe(self._command_topic, qos=self.qos)

    def _on_message(self, client, userdata, message):
        name = message.topic.rsplit("/", 1)[-1]
        payload = json.loads(message.payload)

        if name == "expression":
            expression = payload.get("expression")
            if expression not in KNOWN_EXPRESSIONS:
                expression = DEFAULT_EXPRESSION   # 서버가 값을 늘려도 죽지 않는다
            self.render(expression, payload.get("reason"))

        elif name == "navigate":
            # ← 지금 없는 부분. 도착하면 OK 를 반향해야 서버가 다음 명령을 이어 보낸다.
            self.handle_navigate(payload)

        elif name in ("welcome_start", "welcome_cancel"):
            self.handle_welcome(name, payload)

    # ------------------------------------------------------------------ 이동

    def handle_navigate(self, payload: dict) -> None:
        request_id = payload["requestId"]
        try:
            # 서버가 준 좌표를 그대로 쓴다. 로봇 파라미터의 좌표를 쓰면 출처가 둘이 되어
            # 지도를 다시 그렸을 때 어긋난다.
            arrived = self.goto(payload["x"], payload["y"], payload["yaw"])
        except Exception as exc:  # 이동 예외도 서버에 회신해야 체인이 끊긴 이유가 남는다
            self._reply("navigate", request_id, "ERROR", error=str(exc))
            return
        # OK 는 '도착(도킹 완료)' 을 뜻한다. 출발 시점에 보내면 안 된다.
        self._reply("navigate", request_id, "OK" if arrived else "ERROR")

    def goto(self, x: float, y: float, yaw: float) -> bool:
        raise NotImplementedError

    def render(self, expression: str, reason: str | None) -> None:
        raise NotImplementedError

    def handle_welcome(self, name: str, payload: dict) -> None:
        raise NotImplementedError

    # ------------------------------------------------------------------ 발행

    def _reply(self, command: str, request_id: str, status: str, **extra) -> None:
        # 결과는 반드시 result/ 아래로. command/ 아래에 쓰면 ACL 이 막아 조용히 버려진다.
        self._publish(
            f"potner/device/{self.device_uid}/result/{command}",
            {"requestId": request_id, "status": status, **extra},
        )

    def publish_sensor(self, sensor_type: str, value: float, unit: str) -> None:
        self._publish(self._sensor_topic, {
            "sensorType": sensor_type, "value": round(float(value), 2),
            "unit": unit, "measuredAt": _now(),
        })

    def publish_state(self, state: str) -> None:
        if state not in ROBOT_STATES:
            raise ValueError(f"unknown state: {state}")
        # 같은 상태를 반복해 보내도 된다. 서버가 바뀔 때만 기록한다.
        self._publish(self._state_topic, {"state": state, "changedAt": _now()})

    def publish_battery(self, battery_percent: int) -> None:
        # 정수 0~100. 벗어나면 서버가 버린다. 깎아서 보내지 말고 센서를 확인한다.
        self._publish(self._battery_topic,
                      {"batteryPercent": int(battery_percent), "measuredAt": _now()})

    def publish_heartbeat(self) -> None:
        self._publish(self._heartbeat_topic, {"sentAt": _now()})

    def _publish(self, topic: str, body: dict) -> None:
        # messageId 는 메시지마다 새로. 재사용하면 서버가 중복으로 보고 버린다.
        payload = {"messageId": str(uuid.uuid4()), "deviceId": self.device_uid, **body}
        self._client.publish(topic, json.dumps(payload), qos=self.qos)
```
