# Potner 장치 연동 가이드

라즈베리파이·젯슨을 서버에 붙이려는 사람과, 앱에 기기 연결 화면을 만드는 사람을 위한 문서입니다.
**무엇을 어떻게 보내야 하고, 안 될 때 어디를 보는지**를 다룹니다.

그렇게 설계한 이유는 서버 저장소의 `docs/BACKEND.md` 5절(센서 수집)과 7절(이상 알림)에
있습니다 (이 저장소에는 없는 문서입니다). 두 문서가 어긋나면 코드가 기준입니다.

기준 시점: 2026년 7월 30일.

> **장치 담당자는 아래 문서를 먼저 보세요.** 이 문서는 두 장치의 공통 계약과 설계 근거를
> 다루고, 아래 둘은 각 장치가 할 일만 추려 **현재 동작 상태와 남은 작업 우선순위**를 담고
> 있습니다 (기준 2026-08-03).
>
> - [`DEVICE-RASPBERRY.md`](DEVICE-RASPBERRY.md) — 센서·급수·촬영·송풍·사진 업로드
> - [`DEVICE-JETSON.md`](DEVICE-JETSON.md) — 토양수분·이동·표정·귀가·상태·배터리
>
> 이 문서의 12절 "알려진 공백" 과 14·15절 참고 구현은 그 뒤로 상황이 바뀐 부분이 있습니다.
> 최신 상태는 위 두 문서를 보십시오.

---

## 1. 연결 구조

```
app_user
 └─ robot  (device_uid, name, upload_token_hash)          "본체" 1대
     ├─ iot_device  device_uid=raspberry-01  RASPBERRY_PI
     └─ iot_device  device_uid=jetson-01     JETSON_ORIN

     robot ──── plant_device_assignment ──── plant
                (활성 상태에서 1:1)
```

배정은 **로봇 단위**입니다. 센서값이 들어오면 서버가
`iot_device.device_uid → robot → 활성 배정 → plant` 순으로 거슬러 올라가 어느 식물의 값인지
정합니다. 그래서 파이와 젯슨을 같은 로봇에 달면 둘 다 같은 식물로 귀속됩니다.

활성 배정은 식물당 하나, 로봇당 하나입니다. 재배정은 되고 이력은 남습니다
(`V10__allow_plant_device_reassignment.sql`).

---

## 2. 기기 등록 — 서버에 "이 장치는 내 것"이라고 알리는 단계

**등록이 없으면 장치가 아무리 정확히 보내도 전부 버려집니다.** 실제로 등록 전에 도착한 메시지가
`IoT device was not found`로 버려진 사례가 있었습니다.

등록은 기기당 **한 번**이고 DB에 남습니다. 앱을 껐다 켜도, 재로그인해도, 서버를 재배포해도
유지됩니다. 로그인할 때마다 하는 FCM 토큰 등록과는 성격이 다릅니다.

| 순서 | 엔드포인트 | 본문 |
|---|---|---|
| 1 | `POST /api/v1/plants` | `speciesId`, `lifeStageId`, `name` |
| 2 | `POST /api/v1/robots` | `deviceUid`, `name` → `robotId`, `uploadToken` 반환 |
| 3 | `POST /api/v1/robots/{robotId}/devices` | `deviceUid`, `deviceType` (기기마다 1회) |
| 4 | `POST /api/v1/plants/{plantId}/assignment` | `robotId` |

`deviceType`은 `RASPBERRY_PI` 또는 `JETSON_ORIN`입니다.

`deviceUid` 형식은 `[A-Za-z0-9][A-Za-z0-9_-]*`, 최대 100자입니다. UUID일 필요가 없고, 사용자가
손으로 입력하는 화면을 만들 거라면 `raspberry-01` 같은 짧은 슬러그가 낫습니다.

### 실패 응답

| 상황 | 코드 | HTTP |
|---|---|---|
| 이미 등록된 식별자 | `DEVICE_UID_ALREADY_REGISTERED` | 409 |
| 이미 배정된 로봇 | `ROBOT_ALREADY_ASSIGNED` | 409 |
| 이미 배정된 식물 | `PLANT_ALREADY_ASSIGNED` | 409 |

`device_uid`는 **전역 UNIQUE**입니다. 한 번 등록된 문자열은 다른 사용자가 쓸 수 없습니다.

### 등록이 됐는지 확인하는 방법

`GET /api/v1/plants/{plantId}/devices`가 장치별 `connectionStatus`를 돌려줍니다.
등록 직후에는 `OFFLINE`이고, 장치가 하트비트를 보내기 시작하면 `ONLINE`으로 바뀝니다.

앱에서 사용자가 식별자를 직접 입력하는 화면을 만든다면 **이 폴링을 반드시 넣으세요.**
30~60초 안에 판정되므로 오타를 사용자가 그 자리에서 알 수 있습니다. 이 확인 단계가 없으면
잘못 입력해도 아무 일도 일어나지 않고, 원인을 찾을 방법이 없습니다.

---

## 3. MQTT 접속

| 항목 | 값 |
|---|---|
| 브로커 | `i15e104.p.ssafy.io` |
| 포트 | **1884** (도커 네트워크 내부는 1883) |
| 계정 | 장치별 계정. **`device_uid` 와 같아야 합니다** (`raspberry-01`, `jetson-01`) |
| 비밀번호 | `infra/mosquitto/config/password.txt`. **git에 없고 저장소에 적지 않습니다** |
| QoS | 1 권장 |
| TLS | 없음 (평문) |

**client ID를 서버와 겹치게 쓰면 안 됩니다.** 서버는 **두 개**를 씁니다 — 구독용
`potner-backend-prod`와 발행용 `potner-backend-prod-command`입니다. 같은 값으로 접속하면
브로커가 기존 세션을 끊어 서로를 계속 밀어냅니다. `<device_uid>-collector` 처럼 장치별로
유일한 값을 쓰세요. CI에서 실제로 이 사고가 있었습니다.

계정별 권한은 `infra/mosquitto/config/acl`에 있습니다. 장치 규칙은 계정명으로 자동 매칭되므로
**새 기기를 붙일 때 이 파일을 고칠 필요가 없습니다.**

```
pattern write potner/device/%u/sensor/#
pattern write potner/device/%u/status/#
pattern write potner/device/%u/result/#
pattern read  potner/device/%u/command/#
```

`%u`가 접속 계정명으로 치환되므로 각 장치는 **자기 계정명과 같은 토픽만** 접근할 수 있습니다.
그래서 **mosquitto 계정명이 `device_uid`와 같아야 합니다.** 어긋나면 그 장치는 자기 토픽에
접근하지 못하고, 발행은 조용히 버려져 양쪽 로그에 아무것도 남지 않습니다.

새 기기는 `password.txt`에 계정만 추가하면 됩니다(12절 참고).

---

## 4. 토픽과 `device_uid` 일치 규칙

```
장치 → 서버
  potner/device/<device_uid>/sensor/telemetry     센서 측정값        파이·젯슨
  potner/device/<device_uid>/status/heartbeat     생존 신호          파이·젯슨
  potner/device/<device_uid>/status/state         로봇 행동 상태     젯슨만
  potner/device/<device_uid>/status/battery       배터리 잔량        젯슨만
  potner/device/<device_uid>/status/water-low     스테이션 물 부족   파이만 (18절)
  potner/device/<device_uid>/result/water         급수 결과 회신     파이만 (16절)
  potner/device/<device_uid>/result/capture       촬영 결과 회신     파이만 (16절)
  potner/device/<device_uid>/result/fan           송풍 결과 회신     파이만 (16절)
  potner/device/<device_uid>/result/navigate      이동 결과 회신     젯슨만 (16절)
  potner/device/<device_uid>/result/welcome_start 귀가 마중 도착 회신 젯슨만
  potner/device/<device_uid>/result/welcome_cancel 귀가 취소 회신     젯슨만

서버 → 장치
  potner/device/<device_uid>/command/expression   디스플레이 표정    젯슨이 구독
  potner/device/<device_uid>/command/water        급수 명령          파이가 구독 (16절)
  potner/device/<device_uid>/command/capture      촬영 명령          파이가 구독 (16절)
  potner/device/<device_uid>/command/fan          송풍 명령          파이가 구독 (16절)
  potner/device/<device_uid>/command/navigate     이동 명령          젯슨이 구독 (16절)
  potner/device/<device_uid>/command/welcome_start 귀가 마중 시작     젯슨이 구독
  potner/device/<device_uid>/command/welcome_cancel 귀가 마중 취소    젯슨이 구독
```

**결과 회신은 반드시 `result/` 아래여야 합니다.** `command/water/result` 처럼 `command/` 아래에
쓰면 브로커 ACL 이 장치의 `command/#` 쓰기를 막아 **조용히 버려집니다** — 발행 쪽엔 에러가 없고
브로커 로그에도 남지 않습니다. 장치 ACL 은 `sensor/`·`status/`·`result/` 쓰기와 `command/`
읽기입니다(3절).

**같은 문자열이 네 곳에서 일치해야 합니다.**

| # | 위치 | 대조 방식 |
|---|---|---|
| 1 | 토픽 세그먼트 | 정규식 `^potner/device/([^/]{1,100})/<접미사>$` (신호별로 따로 있음) |
| 2 | 페이로드 `deviceId` | 1번과 Java `String.equals` — **대소문자 구분** |
| 3 | `iot_device.device_uid` | MySQL 조회. 콜레이션이 `ai_ci` 라 **대소문자 무시** |
| 4 | mosquitto 계정명 | 브로커 ACL. 대소문자 구분 |

2번과 3번의 대조 방식이 달라서 이런 비대칭이 생깁니다.

| 토픽 | 페이로드 | DB | 결과 |
|---|---|---|---|
| `raspberry-01` | `raspberry-01` | `raspberry-01` | 정상 |
| `raspberry-01` | `Raspberry-01` | `raspberry-01` | **2번에서 버려짐** |

장치 코드에서 **상수 하나를 토픽 조립과 페이로드에 함께 쓰세요.** 문자열을 두 군데에 따로
적으면 이 함정에 걸립니다.

---

## 5. 센서 페이로드

```json
{
  "messageId": "3f2b1c8a-5d4e-4f6a-9b0c-1d2e3f4a5b6c",
  "deviceId": "raspberry-01",
  "sensorType": "SOIL_MOISTURE",
  "value": 32.5,
  "unit": "PERCENT",
  "measuredAt": "2026-07-29T04:15:00Z"
}
```

**측정값 하나당 메시지 하나입니다.** 온습도·조도까지 보내려면 메시지 4건이고, 각각 `messageId`가
달라야 합니다. 같은 ID로 묶어 보내면 1건만 저장되고 나머지는 조용히 사라집니다.

| `sensorType` | `unit` | 허용 범위 |
|---|---|---|
| `TEMPERATURE` | `CELSIUS` | -40 ~ 85 |
| `HUMIDITY` | `PERCENT` | 0 ~ 100 |
| `SOIL_MOISTURE` | `PERCENT` | 0 ~ 100 |
| `ILLUMINANCE` | `LUX` | 0 이상 |

두 값 모두 서버 enum과 **대소문자까지 정확히** 같아야 합니다. `"soil_moisture"` 처럼 보내면
JSON 파싱 단계에서 통째로 버려집니다.

**배터리는 이 토픽이 아닙니다.** `sensorType`에 `BATTERY`를 넣어 보내면 enum에 없어서 버려집니다.
8절의 전용 토픽을 쓰세요. 이 토픽으로 오는 값은 전부 `plant_id`와 함께 저장되는데, 배터리는
식물의 속성이 아니라 로봇의 속성이라 그 테이블에 들어갈 자리가 없습니다.

`measuredAt`은 오프셋을 포함한 ISO-8601입니다. `Z`도 `+09:00`도 됩니다. **현재 시각보다 10분
이상 미래면 버려지므로 장치의 NTP 동기화가 필요합니다.**

---

## 6. 하트비트 페이로드

```json
{
  "messageId": "9c8b7a6d-...",
  "deviceId": "raspberry-01",
  "sentAt": "2026-07-29T04:15:00Z"
}
```

30초 주기를 권장합니다. 90초간 없으면 서버가 `OFFLINE`으로 표시합니다.

**하트비트를 보내지 않으면 앱의 기기 연결 화면이 영원히 `OFFLINE`입니다.** 센서값만 보내도
데이터는 쌓이지만 사용자에게는 "연결 안 됨"으로 보입니다.

7절과 8절의 신호도 생존 증거로 쳐서 `last_seen_at`을 갱신합니다. 상태나 배터리를 60초 안에
계속 보내고 있다면 하트비트가 없어도 `OFFLINE`이 되지는 않습니다. 그래도 명시적으로 보내는 편이
진단에 좋습니다.

---

## 7. 로봇 행동 상태 — 젯슨만

```
potner/device/<device_uid>/status/state
```

```json
{
  "messageId": "1a2b3c4d-...",
  "deviceId": "jetson-01",
  "state": "NAVIGATING",
  "changedAt": "2026-07-29T04:15:00Z"
}
```

`state` 값 **5종**입니다. 이 외의 값은 JSON 파싱 단계에서 버려집니다.

| 값 | 뜻 |
|---|---|
| `IDLE` | 대기 중 |
| `NAVIGATING` | 이동 중 |
| `DOCKING` | 마커 보며 정밀 접근 중 |
| `SERVICING` | 급수·송풍 받는 중 |
| `GREETING` | 사용자 반기는 중 |

**같은 상태를 계속 반복해서 보내도 됩니다.** 서버 처리가 멱등합니다. 상태가 실제로 바뀔 때만
"바뀐 시각"을 갱신하므로 반복 발행이 그 시각을 밀어내지 않습니다.

`changedAt`은 로그와 진단에만 씁니다. 서버는 **수신 시각**을 저장합니다. 장치 시계 오차가 급수
작업의 타임아웃 계산에 섞이지 않게 하려는 것입니다. 그래도 10분 이상 미래면 버립니다.

**서버가 하는 일**: 현재 상태를 저장해 앱에 노출하고, `SERVICING`·`GREETING`이면 디스플레이
표정을 **매우행복**으로 바꿉니다(9절).

`SERVICING`이 급수와 송풍을 함께 가리켜 서버는 둘을 구별할 수 없습니다. 표정은 어느 쪽이든
같아서 문제가 없지만, 급수 완료 판정에 쓰려면 구분이 필요합니다. 12절 참고.

---

## 8. 배터리 잔량 — 젯슨만

```
potner/device/<device_uid>/status/battery
```

```json
{
  "messageId": "3f2b1c8e-...",
  "deviceId": "jetson-01",
  "batteryPercent": 78,
  "measuredAt": "2026-07-29T04:15:00Z"
}
```

**젯슨만 보냅니다. 라즈베리가 보내면 서버가 거부합니다.**

`robot.battery_percent`가 컬럼 하나라 두 장치가 보내면 서로 덮어써서 값이 흔들립니다. 이동
로봇은 배터리 팩 하나로 두 보드를 돌린다고 보고 젯슨으로 정했습니다. **라즈베리에 별도 배터리가
있다면 알려주세요** — 그러면 스키마를 바꿔야 합니다.

보고하는 장치 종류는 `potner.device.battery-reporter-type`으로 정합니다. 배터리 센서가
라즈베리로 옮겨가면 이미지를 다시 만들지 않고 이 값만 바꿉니다.

| 필드 | 규칙 |
|---|---|
| `batteryPercent` | **정수 0~100** |
| | 범위를 벗어나면 **버립니다.** 0이나 100으로 깎지 않습니다 — 센서 고장을 숨기지 않으려는 것입니다 |
| | 실수를 보내면 내립니다 (`78.9` → `78`). 범위 검사는 내린 뒤 값에 걸리므로 `101.2`는 버려집니다 |
| `messageId` | 중복 제거용이 아닙니다. 단일 컬럼 덮어쓰기라 같은 메시지가 두 번 와도 결과가 같습니다 |
| `measuredAt` | 로그·진단용. 저장은 수신 시각입니다. 10분 이상 미래면 버립니다 |

**60초 주기를 권장합니다.** 배터리는 천천히 변합니다.

같은 값이 다시 와도 "받은 시각"을 갱신합니다. 로봇 상태와 다른 점입니다 — 상태는 "언제부터 이
상태인지"가 필요하고, 배터리는 "이 값이 얼마나 최근인지"가 필요합니다.

배터리 부족 알림은 아직 없습니다. 값만 앱에 노출됩니다.

---

## 9. 디스플레이 표정 — 서버 → 젯슨

```
potner/device/<device_uid>/command/expression
```

```json
{
  "plantId": "20000000-0000-0000-0000-0000000000bb",
  "expression": "HAPPY",
  "reason": "SUNLIGHT"
}
```

젯슨이 `potner/device/<자기 device_uid>/command/#`를 구독하면 받습니다.

| `expression` | 언제 |
|---|---|
| `VERY_HAPPY` | 급수·송풍 받는 중, 사용자 반기는 중 |
| `HAPPY` | 오늘 꽃이 폈거나, 충분한 광량을 받고 있음 |
| `NEUTRAL` | 특별한 일 없음 |
| `SAD` | 온도나 습도가 기준을 벗어남 |

`reason`은 표정이 나온 이유입니다. 말풍선 문구를 고르고 싶을 때 쓰세요. 안 써도 됩니다.
`WATERING` `GREETING` `BLOOMED` `SUNLIGHT` `TEMPERATURE` `HUMIDITY` `NONE`

**두 가지를 지켜야 합니다.**

1. **30초마다 같은 값이 다시 옵니다.** 중복을 걸러낼 필요 없이 마지막에 받은 값을 그리면 됩니다.
   로봇이 재시작해도 30초 안에 표정을 되찾습니다.
2. **모르는 값이 올 수 있습니다.** 서버가 표정이나 이유를 늘릴 수 있으니 알 수 없는 값은 기본
   표정으로 떨어뜨려 주세요. 그러면 서버가 값을 추가할 때 로봇을 고치지 않아도 됩니다.

**배정된 식물이 없으면 아무것도 오지 않습니다.** 서버가 "활성 배정이 있는 식물의 젯슨"에게만
보냅니다. 앱에서 로봇을 식물에 배정해야 표정이 시작됩니다.

---

## 10. 안 될 때 — 로그 문구로 원인 찾기

MQTT는 응답이 없는 단방향이라 장치는 실패를 알 수 없습니다. **서버 로그가 유일한 진단 수단입니다.**

```bash
docker logs --since 5m potner-infra-test-backend-1 2>&1 \
  | grep -iE "sensor reading|heartbeat|robot state|battery|alert|push|command published"
```

검사는 순서대로 이루어지고 단계마다 다른 문구를 남깁니다. 아래는 센서 기준이며, 다른 신호는
`sensor telemetry` 자리에 `heartbeat` · `robot state` · `battery`가 들어갑니다.

| 순서 | 검사 | 실패 시 로그 | 흔한 원인 |
|---|---|---|---|
| 1 | 토픽 정규식 | `Invalid MQTT sensor telemetry topic` | 토픽 오타, 접미사 |
| 2 | JSON 파싱 · enum 값 | `Invalid MQTT sensor telemetry JSON` | enum 대소문자, 깨진 JSON, 서버가 모르는 `state` |
| 3 | 필수값 · 범위 | `Invalid MQTT sensor telemetry values: fields=...` | 누락 필드, 배터리 0~100 초과 (이름이 찍힙니다) |
| 4 | 타입↔단위↔범위 | `Invalid MQTT sensor telemetry type/unit/value` | 5절 표 불일치 (센서 전용) |
| 5 | 미래 시각 | `Invalid MQTT sensor telemetry measuredAt` | 장치 시계 틀어짐 |
| 6 | 토픽 ↔ 페이로드 `deviceId` | `MQTT sensor telemetry deviceId mismatch` | 4절 비대칭 |
| 7 | 저장 | `IoT device was not found` | **2절 등록 안 됨** |
| 7 | 저장 | `robot was not found` | 데이터 정합성 문제 |
| 7 | 저장 | `active plant assignment was not found` | 배정(4단계) 안 함 — 센서값만 해당 |
| 7 | 저장 | `device type is not the configured reporter` | **젯슨이 아닌 장치가 배터리를 보냄** |
| 7 | 저장 | (아무것도 안 나옴) | **`messageId` 재사용**, 또는 아래 "성공 로그가 없는 신호" |
| — | 구독 토픽 아님 | `topic is unsupported` | 서버가 구독하지 않는 토픽 |

성공 로그는 신호마다 다릅니다.

| 신호 | 성공 로그 | 레벨 |
|---|---|---|
| 센서 측정값 | `MQTT sensor reading saved: messageId=..., sensorType=...` | INFO |
| 로봇 상태 | `Robot state changed: deviceId=..., state=...` | INFO — **바뀔 때만** |
| 하트비트 | `MQTT heartbeat processed` | DEBUG |
| 배터리 | `Robot battery recorded` | DEBUG |
| 표정 발행 | `Robot command published: topic=..., payload=...` | INFO |

1~6번은 장치 쪽 문제, 7번은 대체로 서버 등록 문제입니다.

### 성공 로그가 없는 신호

기본 로그 레벨이 INFO라 **하트비트와 배터리는 성공해도 아무 흔적이 없습니다.** 로봇 상태도 같은
상태가 반복되는 동안에는 남지 않습니다(바뀔 때만 INFO).

그 신호들은 로그가 아니라 **결과를 봐야** 합니다.

```bash
# 하트비트 — connectionStatus 가 ONLINE 이면 도착하고 있다
GET /api/v1/plants/{plantId}/devices

# 배터리·로봇 상태 — 같은 응답의 robot 항목
#   batteryPercent, batteryMeasuredAt, currentState, stateChangedAt
```

`batteryMeasuredAt`이 갱신되지 않으면 배터리가 안 들어오고 있는 것입니다.

### `messageId` 재사용은 로그에 안 보입니다

중복 판정만 `log.debug`이고 나머지는 전부 `warn`·`info`입니다. 기본 로그 레벨이 INFO라
**중복으로 버려진 메시지는 아무 흔적도 남기지 않습니다.** 증상은 `saved` 줄이 보내는 건수보다
적다는 것뿐입니다.

센서 4종을 같은 `messageId`로 보내면 1건만 저장되고 3건이 사라지는데, 로그만 보면 원인을 알 수
없습니다. 보낸 건수와 `saved` 건수를 세어 비교하세요.

```bash
docker logs --since 5m potner-infra-test-backend-1 2>&1 | grep -c "sensor reading saved"
```

---

## 11. 알림이 뜨는 조건

측정값이 저장됐다고 알림이 생기는 것은 아닙니다. 다음을 **모두** 만족해야 합니다.

| # | 조건 | 설정 |
|---|---|---|
| 1 | 같은 식물·센서로 3건 이상 | `potner.alert.sample-size` |
| 2 | 3건이 최근 15분 안 | `potner.sensor.freshness-threshold-minutes` |
| 3 | 3건의 **중앙값**이 기준 밖 | 순간 스파이크 방어 |
| 4 | 해당 지표에 열린 알림이 없음 | 활성 알림은 지표별 1건 |

11절 3번 때문에 **3건 중 2건 이상**이 기준 밖이어야 합니다. 45/44/2 처럼 한 건만 튀면 중앙값이 44라
알림이 생기지 않습니다.

**수집 주기가 5분보다 짧아야 합니다.** 주기가 길면 3건을 모으는 사이 첫 건이 신선도 창 밖으로
밀려나 조건이 영원히 차지 않습니다.

### 복귀와 재알림

11절 4번 때문에 한 번 뜬 알림은 정상 범위로 **복귀해야** 다시 뜹니다. 복귀 기준은 진입 기준보다
여유가 있습니다.

```
band = (max − min) × 0.1          potner.alert.hysteresis-ratio
복귀  값 ≥ min + band  (LOW 였던 경우)
      값 ≤ max − band  (HIGH 였던 경우)
```

기준선 근처에서 값이 오갈 때 생성·해제가 반복되는 것을 막기 위함입니다.

지표는 서로 독립입니다. 토양수분 알림이 열려 있어도 온도 알림은 따로 뜹니다.

### 조도는 순간값으로 알림이 나지 않습니다

`ILLUMINANCE`는 저장은 되지만 즉시 판정 대상이 아닙니다. **밤에는 0 lux가 정상**이라 순간
판정을 하면 매일 해 질 때 "조도 부족" 알림이 갑니다. 시드 데이터의 `illuminance_min/max_lux`가
전부 NULL인 것도 같은 이유입니다.

대신 새벽 2시 배치가 하루 누적 광량(`DAILY_LIGHT`)과 일조 시간(`PHOTOPERIOD`)으로 전일분을
판정합니다. 즉석 시연에는 쓸 수 없습니다.

---

## 12. 알려진 공백

지금 구조로 시연은 되지만 제품 흐름으로는 빈 곳이 있습니다. 모르고 순서를 잘못 잡으면 헛일이
되므로 적어 둡니다.

**브로커 계정을 발급할 방법이 없습니다.** ACL은 이제 계정명으로 자동 매칭되므로(3절) 커밋이
필요하지 않지만, `password.txt`가 여전히 수동 관리입니다. 새 기기를 붙이려면 사람이 서버에
들어가 `mosquitto_passwd`를 실행해야 합니다. 앱에 기기 연결 화면을 만들어도 이것이 해결되지
않으면 사용자가 자기 기기를 연결할 수 없습니다. **앱 화면보다 이쪽이 선결입니다.**

**급수 흐름의 서버 쪽은 전부 붙었고, ②→③→⑤→⑦ 을 서버가 자동으로 잇습니다**
(`AutoWateringOrchestrator`, `DEVICE_COMMAND_AUTO_WATER_ENABLED` 기본 켜짐). 수분 부족 알림이
열리면 서버가 사람 없이 이동 → 급수 → 복귀를 순서대로 발행하고, 각 단계는 직전 명령의
`result/...` OK 회신이 와야 이어집니다. **그래서 장치가 결과 회신을 안 보내면 체인이 그 단계에서
멈춥니다.** 남은 것은 장치 쪽 수신 구현입니다.

```
① 라즈베리 → 토양 수분 → 서버                     구현됨
② 서버 판정 → 사용자 알림 "흙이 말랐어요"          구현됨
③ 서버 → 젯슨: 스테이션 이동 명령                  자동 발행됨 (16절) — 젯슨 수신 미구현
④ 젯슨: NAVIGATING → DOCKING → 도착 → 결과 회신    젯슨 미구현 ← ⑤ 가 이 회신을 기다린다
⑤ 서버 → 라즈베리: 급수 명령                       자동 발행됨 (16절)
⑥ 라즈베리: 급수 → 완료 보고                       구현됨 (16절) ← ⑦ 이 이 회신을 기다린다
⑦ 서버 → 젯슨: 대기 장소 복귀 명령                 자동 발행됨 (16절) — 젯슨 수신 미구현
```

**중재 문제는 결정으로 풀렸습니다: 모든 동작 명령은 서버를 거칩니다.** 서버가 생육 데이터
(수분·누적 광량)로 판단해 명령을 내리고, 로봇은 받은 명령만 수행합니다. 이 결정의 로봇 쪽
절반이 남아 있습니다 — **`mission_manager` 의 자율 임무(임계값 판단 → 스스로 이동)를 꺼야
합니다.** 켠 채로 서버 명령을 받으면 두 주인의 명령 사이에서 왕복합니다.

장치 쪽에 남은 구현:

1. **젯슨: `command/navigate` 수신 + `result/navigate` 반향** (16절 계약). 도착이 `OK` 회신으로
   확인돼야 서버가 다음 명령(급수)을 이어 보낼 수 있습니다.
2. **라즈베리: `command/fan` 수신 + `result/fan` 반향** (16절 계약). 팬 하드웨어 제어
   (`cli.fan`)는 이미 있고 MQTT 리스너만 없습니다. **서버는 자동 말리기까지 붙었습니다** —
   토양수분 HIGH 알림이 열리면 스테이션으로 데려가 송풍을 반복하고, 마르면 되돌립니다
   (`AutoDryingScheduler`). 이 수신이 없으면 이동만 하고 송풍 단계에서 타임아웃으로 멈춥니다.
3. **라즈베리: 결과 토픽 두 줄 수정** (16절 경고).

`GREETING`도 서버 명령으로 결정됐습니다. GPS 전에는 앱의 디버그 버튼이
`POST /api/v1/arrival/events`를 호출하고, 이후 Android Geofence가 같은 API를 호출합니다.
서버는 저장된 `GREETING`/`HOME` 좌표를 `welcome_start`에 담아 젯슨에 보냅니다.

**스테이션은 서버에 생겼습니다(17절).** 등록(스테이션 코드)·좌표·물 부족 알림까지 있습니다.
배수트레이 알림은 **센서 없이 누적 급수량으로** 만들었습니다(`DRAINAGE_TRAY`). 급수마다
`device_command.dispensed_ml` 이 쌓이므로 수위 센서를 붙이지 않았습니다 — **장치 쪽 작업이
없습니다.** 임계값은 고정 ml 이 아니라 `recommended_watering_ml × ALERT_DRAINAGE_TRAY_WATERING_MULTIPLIER`
(기본 8회분)입니다. 트레이는 총 배수량으로 차므로 회당 급수량 차이는 무관하지만 트레이 용량은
화분 크기에 비례하기 때문입니다. 사용자가 앱에서 비웠음을 알리면
(`POST /api/v1/plants/{plantId}/drainage-tray/emptied`) 그 시각이 다음 누적의 기준점이 됩니다.

**장치가 필요한 자격증명이 두 개인데 전달 경로가 없습니다.** 센서용 mosquitto 계정과 사진
업로드용 `uploadToken`입니다. 후자는 로봇 등록 응답으로 나오지만 그걸 장치에 넣는 것은 현재
사람이 손으로 합니다.

**사용자가 `device_uid`를 알아낼 방법이 정해지지 않았습니다.** 기기 라벨 QR, 본체 인쇄,
BLE 페어링 중 하나여야 합니다. 블루투스는 서버 작업 범위가 아닙니다.

**기기 해제는 생겼습니다.** `DELETE /api/v1/robots/{robotId}` 가 로봇·하위 장치·활성 배정을
함께 소프트 해제합니다(V25). 측정 이력은 원래 식물에 남고, 해제된 `device_uid` 는 다른 계정이
새로 등록할 수 있습니다. **단 브로커 계정은 남습니다** — 소유권을 넘길 때는
`mosquitto_passwd` 재발급이 별도로 필요합니다. 안 하면 이전 주인이 새 주인의 토픽에 발행할
수 있습니다.

**앱에 기기 등록 화면이 없습니다.** 서버 API는 완비되어 있고 앱이 호출하지 않습니다.

---

## 13. 시연 준비

### 등록 초기화

리허설을 반복하거나 기기 연결 장면을 라이브로 보여주려면 기존 등록을 풀어야 합니다.
**`DELETE /api/v1/robots/{robotId}` 한 번이면 됩니다** — 로봇·하위 장치·배정이 함께 해제되고
같은 코드로 즉시 재등록할 수 있습니다. SQL 이 필요 없습니다.

아래 SQL 은 측정 이력·알림까지 완전히 지우고 싶을 때(활성 알림이 남아 있으면 첫 푸시가 안
나가는 경우 등)만 쓰세요. 계정과 식물은 남기고 기기 쪽만 지웁니다.

RESTRICT 때문에 **순서가 중요합니다.** 안쪽부터 지워야 합니다.

```sql
SET @plant := '<plantId>';
SET @robot := '<robotId>';

START TRANSACTION;
DELETE FROM alert                    WHERE plant_id = @plant;
DELETE FROM sensor_reading           WHERE robot_id = @robot;
DELETE FROM plant_device_assignment  WHERE robot_id = @robot;
DELETE FROM iot_device               WHERE robot_id = @robot;
DELETE FROM robot                    WHERE robot_id = @robot;
COMMIT;
```

`alert`를 함께 지우는 것은 11절 4번 때문입니다. 활성 알림이 남아 있으면 첫 푸시가 가지 않습니다.

### 장치 없이 알림 만들기

하드웨어 없이 알림 경로를 시연하거나 검증할 때 씁니다.

```bash
read -sp 'device pw: ' PW; echo

fire() {   # fire <sensorType> <unit> <value>
  for i in 1 2 3; do
    docker exec potner-infra-test-mosquitto-1 mosquitto_pub -h 127.0.0.1 -p 1883 \
      -u raspberry-01 -P "$PW" \
      -t 'potner/device/raspberry-01/sensor/telemetry' \
      -m "{\"messageId\":\"$(cat /proc/sys/kernel/random/uuid)\",\"deviceId\":\"raspberry-01\",\"sensorType\":\"$1\",\"value\":$3,\"unit\":\"$2\",\"measuredAt\":\"$(date -u +%Y-%m-%dT%H:%M:%SZ)\"}"
    sleep 1
  done
  docker logs --since 1m potner-infra-test-backend-1 2>&1 | grep -iE "result=|Alert push handled" | tail -3
}
```

같은 지표를 LOW → HIGH로 번갈아 쏘면 `result=SWITCHED`가 나면서 기존 알림을 닫고 새 알림을
열어 푸시가 또 갑니다. 11절의 복귀 조건을 맞출 필요 없이 반복 시연이 됩니다.

### 앱 쪽 조건

**debug 빌드여야 합니다.** nginx가 TLS 없이 평문 HTTP만 서빙하는데, `usesCleartextTraffic`이
debug 소스셋에만 있어서 release 빌드는 서버에 닿지 못합니다.

```bash
flutter run --dart-define=API_BASE_URL=http://i15e104.p.ssafy.io/api/v1
```

알림이 뜨는 순간 **앱은 백그라운드에 있어야 합니다.** 포그라운드면 시스템 알림 대신 앱이 직접
처리해서 화면에 안 보일 수 있습니다.

---

## 14. 라즈베리파이 참고 구현

`Raspberry-master`의 `sensor-collector`는 현재 존재하지 않는 `POST /api/v1/sensors/soil`로
HTTP POST를 하고 있습니다. 대상도 `http://127.0.0.1:8080`(파이 자신)이고 인증도 없습니다.
아래 형태로 바꿔야 합니다. `requirements.txt`에 `paho-mqtt>=2.0` 추가가 필요합니다.

```python
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import paho.mqtt.client as mqtt

# 서버 enum 과 정확히 일치해야 한다. 대소문자도 구분한다.
SENSOR_UNITS = {
    "TEMPERATURE": "CELSIUS",     # -40 ~ 85
    "HUMIDITY": "PERCENT",        # 0 ~ 100
    "SOIL_MOISTURE": "PERCENT",   # 0 ~ 100
    "ILLUMINANCE": "LUX",         # 0 이상
}


@dataclass(frozen=True)
class PublishResult:
    ok: bool
    message_id: Optional[str]
    message: str


class MqttTelemetryPublisher:
    """측정값을 MQTT 로 서버에 보낸다. 측정값 하나당 메시지 하나."""

    def __init__(
        self,
        host: str,
        port: int = 1884,
        *,
        username: str,
        password: str,
        device_uid: str = "raspberry-01",
        qos: int = 1,
    ) -> None:
        self.host, self.port = host, port
        self.username, self.password = username, password
        self.device_uid = device_uid
        self.qos = qos
        # 토픽 세그먼트와 페이로드 deviceId 는 서버가 문자열 비교로 대조한다.
        # 대소문자까지 같아야 하므로 반드시 같은 변수에서 만든다.
        self.topic = f"potner/device/{device_uid}/sensor/telemetry"
        self._client: Optional[mqtt.Client] = None

    def connect(self) -> None:
        # client_id 는 브로커에서 유일해야 한다. 서버가 potner-backend-prod 를 쓰므로
        # 겹치면 브로커가 서로를 계속 끊어낸다.
        client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=f"{self.device_uid}-collector",
        )
        client.username_pw_set(self.username, self.password)
        client.connect(self.host, self.port, keepalive=60)
        client.loop_start()
        self._client = client

    def publish(self, sensor_type: str, value: float) -> PublishResult:
        if self._client is None:
            return PublishResult(False, None, "not connected")

        unit = SENSOR_UNITS.get(sensor_type)
        if unit is None:
            return PublishResult(False, None, f"unknown sensorType: {sensor_type}")

        payload = {
            # 측정값마다 새로 만든다. 재사용하면 서버가 중복으로 보고 버린다.
            "messageId": str(uuid.uuid4()),
            "deviceId": self.device_uid,
            "sensorType": sensor_type,
            "value": round(float(value), 2),
            "unit": unit,
            # 서버 시각보다 10분 이상 미래면 버려진다. NTP 동기화가 필요하다.
            "measuredAt": datetime.now(timezone.utc)
                .isoformat(timespec="seconds")
                .replace("+00:00", "Z"),
        }

        info = self._client.publish(self.topic, json.dumps(payload), qos=self.qos)
        info.wait_for_publish(timeout=5)
        return PublishResult(
            info.rc == mqtt.MQTT_ERR_SUCCESS, payload["messageId"], "published"
        )

    def close(self) -> None:
        if self._client is not None:
            self._client.loop_stop()
            self._client.disconnect()
```

호출부입니다. 비밀번호는 환경변수로 받고 코드나 git에 넣지 않습니다.

```python
pub = MqttTelemetryPublisher(
    host="i15e104.p.ssafy.io",
    username="raspberry-01",
    password=os.environ["MQTT_PASSWORD"],
)
pub.connect()

pub.publish("SOIL_MOISTURE", reading["soil_moisture_pct"])
pub.publish("TEMPERATURE",   reading["temperature_c"])
pub.publish("HUMIDITY",      reading["humidity_pct"])
pub.publish("ILLUMINANCE",   reading["lux"])
```

하트비트는 같은 클라이언트로 `potner/device/<uid>/status/heartbeat`에 6절 페이로드를 30초마다
발행하면 됩니다.

---

## 15. 젯슨 참고 구현

젯슨은 파이와 달리 **보내는 것 셋, 받는 것 하나**입니다.

```python
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

import paho.mqtt.client as mqtt

DEVICE_UID = "jetson-01"

# 5종. 서버 enum 과 대소문자까지 같아야 한다. 그 외 값은 서버가 버린다.
ROBOT_STATES = {"IDLE", "NAVIGATING", "DOCKING", "SERVICING", "GREETING"}

# 서버가 늘릴 수 있다. 모르는 값이 오면 기본 표정으로 떨어뜨린다.
KNOWN_EXPRESSIONS = {"VERY_HAPPY", "HAPPY", "NEUTRAL", "SAD"}
DEFAULT_EXPRESSION = "NEUTRAL"


def _now() -> str:
    # 서버 시각보다 10분 이상 미래면 버려진다. NTP 동기화가 필요하다.
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


class JetsonBridge:
    def __init__(self, host: str, *, username: str, password: str, port: int = 1884):
        self.device_uid = username  # 계정명 == device_uid == 토픽 세그먼트
        self.qos = 1
        # 토픽과 페이로드 deviceId 를 같은 변수에서 만든다. 두 군데에 따로 적으면
        # 대소문자가 어긋나 서버가 버린다(4절).
        self._state_topic = f"potner/device/{self.device_uid}/status/state"
        self._battery_topic = f"potner/device/{self.device_uid}/status/battery"
        self._command_topic = f"potner/device/{self.device_uid}/command/#"

        # client_id 는 브로커에서 유일해야 한다. 서버가 potner-backend-prod 와
        # potner-backend-prod-command 를 쓰므로 겹치면 서로를 계속 끊어낸다.
        self._client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=f"{self.device_uid}-bridge",
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
        if not message.topic.endswith("/command/expression"):
            return
        payload = json.loads(message.payload)
        expression = payload.get("expression")
        if expression not in KNOWN_EXPRESSIONS:
            expression = DEFAULT_EXPRESSION
        # 30초마다 같은 값이 다시 온다. 중복을 걸러낼 필요 없이 그대로 그리면 되고,
        # 재시작해도 한 주기 안에 표정을 되찾는다.
        self.render(expression, payload.get("reason"))

    def render(self, expression: str, reason: str | None) -> None:
        raise NotImplementedError

    def publish_state(self, state: str) -> None:
        if state not in ROBOT_STATES:
            raise ValueError(f"unknown state: {state}")
        # 같은 상태를 반복해 보내도 된다. 서버가 바뀔 때만 기록한다.
        self._publish(self._state_topic, {"state": state, "changedAt": _now()})

    def publish_battery(self, battery_percent: int) -> None:
        # 정수 0~100. 범위를 벗어나면 서버가 버린다. 깎아서 보내지 말고 센서 값을 확인한다.
        self._publish(
            self._battery_topic,
            {"batteryPercent": int(battery_percent), "measuredAt": _now()},
        )

    def _publish(self, topic: str, body: dict) -> None:
        payload = {"messageId": str(uuid.uuid4()), "deviceId": self.device_uid, **body}
        self._client.publish(topic, json.dumps(payload), qos=self.qos)
```

하트비트는 파이와 같습니다. 젯슨도 `status/heartbeat`를 30초마다 보내야 앱의 기기 목록에서
`ONLINE`으로 보입니다.

배터리와 상태는 성공해도 서버 로그에 남지 않습니다(10절). 도착하는지는
`GET /api/v1/plants/{plantId}/devices` 응답의 `batteryMeasuredAt`·`currentState`로 확인하세요.

---

## 16. 급수·촬영 명령과 결과 — 서버 ↔ 라즈베리

서버가 명령을 내리고 라즈베리가 결과를 회신하는 첫 양방향 경로입니다. 계약은 라즈베리 쪽
구현(`src/mqtt/water_command.py`, `capture_command.py`)을 서버가 따라간 것이므로, 장치 쪽은
**결과 토픽만 고치면** 지금 코드 그대로 붙습니다.

```
서버 → 파이   potner/device/<device_uid>/command/water     {"ml": 350.00, "requestId": "<uuid>"}
파이 → 서버   potner/device/<device_uid>/result/water      아래 참조
서버 → 파이   potner/device/<device_uid>/command/capture   {"requestId": "<uuid>"}
파이 → 서버   potner/device/<device_uid>/result/capture    아래 참조
```

**⚠ 라즈베리 설정 수정이 필요합니다.** 현재 `config/raspberry_pi.yaml` 의 결과 토픽이
`command/water/result` 인데, 브로커 ACL 이 장치의 `command/#` 쓰기를 막아 **회신이 조용히
버려집니다.** 두 줄을 바꾸세요.

```yaml
water_result_topic:   potner/device/raspberry-01/result/water
capture_result_topic: potner/device/raspberry-01/result/capture
```

### 결과 페이로드

```json
{
  "messageId": "<uuid, 회신마다 새로>",
  "deviceId": "raspberry-01",
  "requestId": "<명령에서 받은 값 그대로>",
  "status": "OK",
  "requestedMl": 350.0,
  "dispensedMl": 348.5,
  "durationSec": 13.9,
  "capped": false,
  "measuredAt": "2026-07-30T06:01:47Z"
}
```

| status | 뜻 | 서버 처리 |
|---|---|---|
| `OK` | 수행 완료 | `dispensedMl` 을 급수 이력에 기록. 일기의 급수량이 이 값의 합이다 |
| `ERROR` | 실패. `error`(문구)와 촬영은 `code` 도 실림 | 사유를 이력에 기록 |
| `BUSY` | 앞선 작업 중이라 거부 | 실패와 구분해 기록. 잠시 뒤 다시 보내면 된다 |

`requestId` 반향이 전부입니다. 서버는 이 값으로 어느 명령의 결과인지 대조하므로, 받은 값을
그대로 되돌려야 합니다. `dispensedMl` 은 요청량과 다를 수 있습니다(펌프 상한 도달 등) —
실제로 나간 양을 보내야 일기가 맞습니다.

### 이동 명령 — 서버 → 젯슨 (수신 구현 필요)

모든 동작 명령이 서버를 거치기로 결정되면서 이동도 이 계약을 씁니다. **젯슨 브릿지가
`command/navigate` 를 구독하고 `result/navigate` 로 반향하는 구현이 필요합니다** — 현재
브릿지는 `command/expression` 만 처리합니다.

```
서버 → 젯슨   potner/device/<device_uid>/command/navigate
{"destination": "WATER_STATION", "x": 1.250, "y": -0.480, "yaw": 1.5708, "requestId": "<uuid>"}

젯슨 → 서버   potner/device/<device_uid>/result/navigate
{"messageId": "<uuid>", "deviceId": "jetson-01", "requestId": "<그대로 반향>",
 "status": "OK", "measuredAt": "..."}
```

- **좌표를 그대로 쓰세요.** map 프레임(m, rad)이며 출처는 서버의 위치 저장소(17절)입니다.
  로봇 파라미터의 `station_poses` 는 쓰지 않습니다 — 좌표의 출처가 둘이 되면 어긋납니다.
- `destination` 은 로그와 상태 보고용 이름입니다(WATER_STATION/HOME/SUNLIGHT/GREETING).
- `OK` 는 **도착(도킹 완료)** 을 뜻합니다. 이 회신이 와야 서버가 다음 명령(급수 등)을 이어
  보냅니다. 실패는 `ERROR` + `error` 문구(예: DOCKING_FAILED, NAVIGATION_FAILED), 이동 중
  새 명령은 `BUSY` 입니다.
- 자율 임무는 꺼야 합니다(12절). 서버 명령만이 이동의 트리거입니다.

### 귀가 마중 명령 — 서버 → 젯슨

GPS 전에는 앱의 디버그 버튼, GPS 적용 뒤에는 Android Geofence가 같은 API와 MQTT 계약을
사용합니다. `requestId`는 `eventId`와 같고 QoS 1 중복 수신에도 같은 이동을 다시 시작하지
않아야 합니다.

```
서버 → 젯슨   potner/device/<device_uid>/command/welcome_start
{"eventId":"<uuid>","visitId":"<uuid>","requestId":"<eventId와 같음>",
 "destination":"GREETING","x":1.25,"y":-0.48,"yaw":1.5708,
 "returnDestination":"HOME","returnX":0.10,"returnY":0.20,"returnYaw":0.0,
 "waitSeconds":120,"totalTimeoutSeconds":300,"publishedAt":"..."}

젯슨 → 서버   potner/device/<device_uid>/result/welcome_start
{"messageId":"<새 uuid>","deviceId":"jetson-01","requestId":"<그대로 반향>",
 "status":"OK"}
```

`welcome_start`의 `OK`는 **GREETING 좌표 도착**을 뜻합니다. 젯슨은 도착 뒤 사람을 인식하면
인사하고 HOME으로 복귀합니다. 사람이 보이지 않으면 `waitSeconds` 뒤 복귀하고,
`totalTimeoutSeconds`를 넘기면 기다리지 않고 HOME 복귀를 시작합니다.

```
서버 → 젯슨   potner/device/<device_uid>/command/welcome_cancel
{"eventId":"<새 uuid>","visitId":"<start와 같음>","requestId":"<eventId와 같음>",
 "returnDestination":"HOME","returnX":0.10,"returnY":0.20,"returnYaw":0.0,
 "publishedAt":"..."}

젯슨 → 서버   potner/device/<device_uid>/result/welcome_cancel
{"messageId":"<새 uuid>","deviceId":"jetson-01","requestId":"<그대로 반향>",
 "status":"OK"}
```

취소 `OK`는 **HOME 좌표 도착**을 뜻합니다. 이동 실패는 `ERROR`, 다른 방문이나 다른 임무를
수행 중이라 시작하지 못하면 `BUSY`입니다. 실패에는 `error`와 기계 판독용 `code`를 함께
보낼 수 있습니다.

### 송풍 명령 — 서버 → 라즈베리 (수신 구현 필요)

토양 수분 과다를 말리는 명령입니다. 팬 하드웨어 제어(`cli.fan`)는 이미 있으므로
급수 수신기와 같은 모양의 MQTT 리스너만 붙이면 됩니다.

```
서버 → 파이   potner/device/<device_uid>/command/fan
{"seconds": 30, "requestId": "<uuid>"}

파이 → 서버   potner/device/<device_uid>/result/fan
{"messageId": "<uuid>", "deviceId": "raspberry-01", "requestId": "<그대로 반향>",
 "status": "OK", "measuredAt": "..."}
```

가동 시간을 서버가 정합니다. 말리기는 "짧은 가동 → 수분 재측정 → 필요하면 재가동" 의
반복이라 한 번에 오래 돌리지 않습니다 — 과건조는 되돌릴 수 없습니다.

### 서버 쪽 동작

- 발행 주체: `POST /api/v1/plants/{plantId}/device-commands`
  `{"type":"WATER"|"CAPTURE"|"FAN"|"NAVIGATE", "destination"?: ..., "seconds"?: ...}`.
  급수량은 요청이 아니라 식물의 적용 생육 기준(`recommendedWateringMl`)에서 나옵니다.
  NAVIGATE 는 목적지의 지도 좌표(17절)가 입력되어 있어야 발행됩니다.
- 같은 식물·같은 종류가 회신 대기 중이면 409 로 거절합니다.
- 회신이 120초(기본) 안에 없으면 서버가 `TIMED_OUT` 으로 끊습니다. 늦게 온 회신은 그 위에
  덮어씁니다 — 타임아웃은 추정이고 회신은 물리적 사실입니다.
- 회신 반영 로그: `Device command result applied: requestId=..., status=..., dispensedMl=...`
  실패 유형별로 `unknown request` / `deviceId mismatch` / `already applied` 가 따로 남습니다.

---

## 17. 위치 등록 — 스테이션 코드와 지도 좌표

로봇이 오가는 위치 4종을 서버가 저장합니다. 물리 장치는 급수 스테이션뿐이라 그것만 코드를
갖고, 나머지는 SLAM 지도 위 좌표입니다.

| type | 무엇 | stationCode |
|---|---|---|
| `WATER_STATION` | 급수 스테이션 | 필수. 전역 UNIQUE, 형식은 device_uid 와 동일 |
| `HOME` | 대기 장소(그늘) | 없음 |
| `SUNLIGHT` | 햇빛 자리 | 없음 |
| `GREETING` | 마중 지점 | 없음 |

```
POST /api/v1/robots/{robotId}/locations              등록 (앱의 "스테이션 코드" 입력이 여기)
GET  /api/v1/robots/{robotId}/locations              목록
PUT  /api/v1/robots/{robotId}/locations/{type}/pose  좌표 입력 {"x","y","yaw"}
```

좌표는 등록과 **분리**되어 있습니다. 코드는 사용자가 앱에서 넣고, 좌표는 지도를 만든 설치자가
RViz 에서 읽어 PUT 으로 넣습니다. 좌표가 없는 동안 `poseConfigured=false` 이며 그 위치로는
로봇을 보낼 수 없습니다. **지도를 다시 그리면 모든 좌표를 다시 넣어야 합니다** — 원점이 바뀌어
이전 좌표는 엉뚱한 곳을 가리킵니다.

미설정은 0,0,0 이 아니라 NULL 입니다. 0,0,0 은 지도 원점이라는 실제 좌표라서, 로봇 쪽 규약
("전부 0 이면 미설정")과 다릅니다. 서버에서 좌표를 받아 쓸 때 이 차이를 옮기지 마세요.

---

## 18. 스테이션 물 부족 보고 — 파이 → 서버

스테이션 수위 센서를 읽는 라즈베리가 보냅니다. 이 절의 계약은 서버가 정의했고 장치가 따라
구현합니다 — 하트비트·배터리와 같은 방식입니다.

```
potner/device/<device_uid>/status/water-low
{"messageId": "<uuid>", "deviceId": "raspberry-01", "waterLow": true, "measuredAt": "2026-07-30T06:00:00Z"}
```

- **불리언입니다.** 수위 퍼센트가 아닙니다. 임계값 판정은 센서를 아는 장치가 합니다.
- **`waterLow: false` 도 보내야 합니다.** 물을 보충하면 false 를 보내야 서버 플래그가 내려가고,
  그래야 다음 부족 때 알림이 다시 나갑니다. 상태가 바뀔 때와 주기 보고 어느 쪽이든 됩니다 —
  서버 처리가 멱등해서 같은 상태를 반복 보고해도 알림이 쏟아지지 않습니다.
- 사용자 푸시는 부족으로 **바뀔 때 한 번만** 나갑니다. 로봇에 식물이 배정되어 있으면 앱 알림
  목록(`GET /api/v1/alerts`)에도 남습니다 — `metricType: STATION_WATER_LOW`, 측정값·기준은
  null 입니다. 배정 전에는 알림을 매달 식물이 없어 푸시만 나갑니다.
- 로봇에 급수 스테이션이 등록(17절)되어 있어야 합니다. 없으면 서버 로그에
  `the robot has no water station registered` 로 버려집니다 — "알림이 왜 안 오지" 는 이것부터
  확인하세요.
