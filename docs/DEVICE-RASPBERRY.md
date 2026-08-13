# 라즈베리파이 ↔ 서버 연동 명세

기준 시점: 2026년 8월 3일. 서버는 `Server-master`(2ff54e5) 가 EC2 에 배포되어 있고
**서버 쪽 수정 없이** 이 문서대로 구현하면 붙는다.

> **문서 세 개의 관계**
> - [`DEVICE-MQTT.md`](DEVICE-MQTT.md) — 장치 공통 계약과 설계 근거. 두 장치가 함께 지켜야 할 규칙
> - **`DEVICE-RASPBERRY.md`** (이 문서) — 라즈베리가 할 일만 추린 것. 현재 동작 상태 포함
> - [`DEVICE-JETSON.md`](DEVICE-JETSON.md) — 젯슨이 할 일
>
> 세 문서가 어긋나면 **코드가 기준**이다. 설계를 왜 그렇게 했는지는 `DEVICE-MQTT.md` 와
> `BACKEND.md`(서버 저장소에 있음 — 이 저장소에는 없다) 에 있다.

라즈베리파이는 **스테이션에 고정**되어 있다. 펌프·카메라·팬·온습도·조도 센서가 모두 여기 달려
있고, 화분(젯슨)이 스테이션으로 찾아와 급수·촬영·송풍을 받는다.

---

## 0. 지금 상태 요약

| 기능 | 구현 | 실제 동작 |
|---|---|---|
| 온습도 전송 | ✅ | ✅ **10초마다 들어오고 있음** |
| 조도 전송 | ✅ | 🔴 7/31 이후 끊김 |
| 하트비트 | ✅ | ✅ 정상 (ONLINE) |
| 급수 명령 수신 | ✅ `water_command.py` | ❓ 실제 펌프가 도는지 확인 필요 (`pump.driver`) |
| 촬영 명령 수신 | ✅ `capture_command.py` | ✅ OK 회신은 옴 |
| **사진 업로드** | 🔴 **없음** | 🔴 찍어서 로컬에만 저장 |
| **송풍 명령 수신** | 🔴 **없음** | 하드웨어 제어(`cli.fan`)는 있음 |
| **스테이션 물 부족 보고** | 🔴 **없음** | |
| 결과 토픽 경로 | ✅ `Raspberry-develop` 에서 수정됨 | ⚠️ **`Raspberry-master` 는 7/21 에 멈춰 있음** |

### ⚠️ 가장 먼저 확인할 것

**파이에 어느 브랜치가 올라가 있는가.** `Raspberry-master` 는 07-21 이후 멈춰 있어
`water_command.py` · `capture_command.py` · MQTT 연동이 **전부 없다**. `Raspberry-develop` 에만
있다. master 기준으로 배포 중이면 급수·촬영 명령을 아예 받지 못한다.

```bash
git -C <저장소> branch --show-current
git -C <저장소> log --oneline -1
```

---

## 1. 브로커 접속

| 항목 | 값 |
|---|---|
| 호스트 | `i15e104.p.ssafy.io` |
| 포트 | **1884** (도커 내부는 1883) |
| 계정 | `raspberry-01` — **`device_uid` 와 같아야 한다** |
| 비밀번호 | 팀 공유. `password.txt` 는 git 에 없다 |
| QoS | 1 |
| TLS | 없음 (평문) |
| keepalive | 60초 권장 |

### client_id 는 유일해야 한다

서버가 **두 개**를 쓴다 — 구독용 `potner-backend-prod`, 발행용 `potner-backend-prod-command`.
겹치면 브로커가 기존 세션을 끊어 서로를 계속 밀어낸다. **CI 에서 실제로 이 사고가 있었다.**

```python
client_id = f"{device_uid}-collector"   # 예: raspberry-01-collector
```

### ACL

계정명으로 자동 매칭되므로 새 기기를 붙일 때 파일을 고칠 필요가 없다.

```
pattern write potner/device/%u/sensor/#
pattern write potner/device/%u/status/#
pattern write potner/device/%u/result/#
pattern read  potner/device/%u/command/#
```

`%u` 가 접속 계정명으로 치환된다. **자기 계정명과 같은 토픽만** 접근할 수 있다.

> 🔴 **`command/` 아래에 쓰면 조용히 버려진다.** 발행 쪽에 에러가 없고 브로커 로그에도 안 남는다.
> 결과는 반드시 `result/` 아래로 보낸다.

---

## 2. `device_uid` — 네 곳이 같아야 한다

| # | 위치 | 대조 방식 |
|---|---|---|
| 1 | MQTT 토픽 세그먼트 | 정규식 `^potner/device/([^/]{1,100})/<접미사>$` |
| 2 | 페이로드 `deviceId` | 1번과 Java `String.equals` — **대소문자 구분** |
| 3 | DB `iot_device.device_uid` | MySQL 조회. 콜레이션 `ai_ci` 라 대소문자 무시 |
| 4 | mosquitto 계정명 | 브로커 ACL. **대소문자 구분** |

2번과 3번의 대조 방식이 달라 이런 비대칭이 생긴다.

| 토픽 | 페이로드 | DB | 결과 |
|---|---|---|---|
| `raspberry-01` | `raspberry-01` | `raspberry-01` | 정상 |
| `raspberry-01` | `Raspberry-01` | `raspberry-01` | **2번에서 버려짐** |

**코드에서 상수 하나를 토픽 조립과 페이로드에 함께 쓴다.** 두 군데 따로 적으면 이 함정에 걸린다.

### 등록이 안 되어 있으면 전부 버려진다

앱에서 기기 등록을 마쳐야 서버가 받는다. 등록 전 메시지는 `IoT device was not found` 로 버려진다.
현재 `raspberry-01` 은 `robot-01`(dudu 로봇) 아래에 등록되어 `dudu` 식물에 배정되어 있다.

---

## 3. 시각 규약

모든 시각은 **오프셋을 포함한 ISO-8601** 이다. `Z` 를 권장한다.

```
2026-08-03T02:15:00Z        (권장)
2026-08-03T11:15:00+09:00   (허용. 단 쿼리 스트링에서는 + 가 깨진다)
```

🔴 **현재 시각보다 10분 이상 미래면 버려진다.** NTP 동기화가 필요하다.

---

## 4. 센서 측정값 — 파이가 보내는 것

```
potner/device/raspberry-01/sensor/telemetry
```

```json
{
  "messageId": "3f2b1c8a-5d4e-4f6a-9b0c-1d2e3f4a5b6c",
  "deviceId": "raspberry-01",
  "sensorType": "TEMPERATURE",
  "value": 24.8,
  "unit": "CELSIUS",
  "measuredAt": "2026-08-03T02:15:00Z"
}
```

| `sensorType` | `unit` | 허용 범위 | 담당 |
|---|---|---|---|
| `TEMPERATURE` | `CELSIUS` | −40 ~ 85 | **파이** |
| `HUMIDITY` | `PERCENT` | 0 ~ 100 | **파이** |
| `ILLUMINANCE` | `LUX` | 0 이상 | **파이** |
| `SOIL_MOISTURE` | `PERCENT` | 0 ~ 100 | 젯슨 (센서가 화분에 있음) |

두 값 모두 서버 enum 과 **대소문자까지 정확히** 같아야 한다. `"soil_moisture"` 처럼 보내면 JSON
파싱 단계에서 통째로 버려진다.

### 🔴 측정값 하나당 메시지 하나, `messageId` 는 매번 새로

`sensor_reading.device_message_id` 가 **전역 UNIQUE** 다. QoS 1 재전송 방어용이다.
온습도·조도 세 값을 같은 `messageId` 로 보내면 **1건만 저장되고 나머지는 조용히 사라진다.**

중복 판정은 `log.debug` 라 기본 로그 레벨(INFO)에서는 **아무 흔적도 남지 않는다.** 증상은
`saved` 줄이 보낸 건수보다 적다는 것뿐이다.

### 수집 주기

**5분보다 짧아야 한다.** 이상 알림이 뜨려면 "최근 15분 안에 3건" 이 필요한데, 주기가 길면 3건을
모으는 사이 첫 건이 신선도 창 밖으로 밀려나 조건이 영원히 차지 않는다. 현재 10초 주기로
들어오고 있어 문제없다.

### 조도가 끊겨 있다

7/31 이후 `ILLUMINANCE` 가 안 들어온다. 조도는 **하루 누적 광량 판정**의 유일한 입력이다.
없으면 커버리지 미달로 `INSUFFICIENT_DATA` 가 되어 앱의 일일 광량 화면이 빈다.

> 참고: 조도 센서가 스테이션에 고정이면 화분이 햇빛 자리로 이동해도 측정값이 안 오른다.
> 서버의 자동 일광 이동이 목표를 채우지 못해 창(8~17시)이 닫힐 때까지 화분이 햇빛 자리에
> 머문다. 조도 센서를 젯슨(화분)으로 옮기면 이 문제가 사라진다.

---

## 5. 하트비트

```
potner/device/raspberry-01/status/heartbeat
```

```json
{
  "messageId": "9c8b7a6d-...",
  "deviceId": "raspberry-01",
  "sentAt": "2026-08-03T02:15:00Z"
}
```

**30초 주기 권장.** 90초간 없으면 서버가 `OFFLINE` 으로 표시한다.

보내지 않으면 **앱의 장치 관리 화면이 영원히 OFFLINE** 이다. 센서값만 보내도 데이터는 쌓이지만
사용자에게는 "연결 안 됨" 으로 보인다.

성공해도 서버 로그에 안 남는다(DEBUG 레벨). 확인은 `GET /api/v1/plants/{plantId}/devices` 의
`connectionStatus` 로 한다.

---

## 6. 급수 — `command/water` 수신 → `result/water` 회신

### 받는 것

```
potner/device/raspberry-01/command/water
{"ml": 200.00, "requestId": "a7ae63a0-c294-4a8d-a111-ed698f1c2ef0"}
```

**급수량을 서버가 정한다.** 앱의 케어 설정에 있는 식물별 `recommendedWateringMl` 이 유일한
출처다. 파이가 임의로 바꾸면 앱에 보이는 값과 실제 급수가 달라진다.

### 보내는 것

```
potner/device/raspberry-01/result/water
```

```json
{
  "messageId": "<회신마다 새 uuid>",
  "deviceId": "raspberry-01",
  "requestId": "<명령에서 받은 값 그대로>",
  "status": "OK",
  "requestedMl": 200.0,
  "dispensedMl": 198.5,
  "durationSec": 8.2,
  "capped": false,
  "measuredAt": "2026-08-03T02:15:08Z"
}
```

| `status` | 뜻 | 서버 처리 |
|---|---|---|
| `OK` | 수행 완료 | `dispensedMl` 을 급수 이력에 기록. **일기의 급수량과 배수트레이 누적이 이 값의 합이다** |
| `ERROR` | 실패. `error` 문구를 함께 | 사유를 이력에 기록. 자동 체인이 거기서 멈춤 |
| `BUSY` | 앞선 작업 중이라 거부 | 실패와 구분해 기록. 잠시 뒤 다시 보내면 된다 |

### 🔴 `requestId` 반향이 전부다

서버는 이 값으로 어느 명령의 결과인지 대조한다. 받은 값을 **그대로** 되돌려야 한다.
틀리면 `unknown request` 로 버려진다.

### `dispensedMl` 은 실측값이어야 한다

요청량과 다를 수 있다(펌프 상한 도달 등). 서버는 이 값을 그대로 믿고 일기와 배수트레이
누적에 쓴다. **`flow_ml_per_sec` 보정이 틀리면 그 두 값도 틀어진다.**

```bash
python -m cli.water --config config/raspberry_pi.yaml calibrate
```

### 타임아웃

**120초** 안에 회신이 없으면 서버가 `TIMED_OUT` 으로 끊는다. 늦게 온 회신은 그 위에 덮어쓴다 —
타임아웃은 추정이고 회신은 물리적 사실이기 때문이다.

### 자동 급수 체인에서의 위치

```
① 젯슨 토양수분 40% 미만 3건 → 서버 판정 → 알림
② 서버 → 젯슨:  NAVIGATE(WATER_STATION)     화분이 스테이션으로 온다
③ 젯슨 → 서버:  result/navigate OK          도착
④ 서버 → 파이:  command/water {ml}          ← 여기
⑤ 파이 → 서버:  result/water OK             급수 완료
⑥ 서버 → 젯슨:  NAVIGATE(HOME)              화분이 돌아간다
```

**④가 오기 전에 화분은 이미 스테이션에 도착해 있다.** 수동으로 급수 명령을 쏠 때는 화분 위치를
서버가 확인하지 않으므로, 화분이 없는 상태에서 펌프가 돌 수 있다는 점에 주의한다.

---

## 7. 촬영 — `command/capture` 수신 → 사진 업로드 → `result/capture` 회신

### 받는 것

```
potner/device/raspberry-01/command/capture
{"requestId": "<uuid>"}
```

### 🔴 사진 업로드가 빠져 있다

현재 `capture_command.py` 는 **찍어서 로컬(`data/camera`)에 저장하고 OK 만 회신**한다.
서버로 올리는 코드가 없다. 확인된 증상:

```
device_command  CAPTURE  status=OK      ← 서버는 성공으로 기록
plant_photo     새 행 0건
디스크          사진 파일 0개
nginx           /device/photos 요청 0건
```

**사진 업로드가 포토 로그·타임랩스·성장 비교·일기 사진·생장 단계 자동 판정·자동 개화 기록의
유일한 입구다.** 이게 없으면 그 여섯 기능이 전부 빈 화면이다.

### 업로드 API (HTTP, MQTT 아님)

```
POST http://i15e104.p.ssafy.io/api/v1/device/photos
```

| 항목 | 값 |
|---|---|
| Content-Type | `multipart/form-data` |
| 파일 파트 이름 | **`file`** (다른 이름이면 400) |
| 인증 헤더 | **`X-Device-Token: <uploadToken>`** |
| 쿼리 파라미터 | `capturedAt` (선택). **`Z` 형식만** — 쿼리에서 `+` 가 공백으로 해석된다 |
| 성공 | **201 Created** |
| 최대 크기 | 10 MiB |

**`plantId` 를 보내지 않는다.** 서버가 업로드 토큰 → 로봇 → 활성 배정 순으로 식물을 정한다.

| 코드 | 뜻 | 대응 |
|---|---|---|
| 201 | 성공 | |
| 401 `INVALID_DEVICE_TOKEN` | 토큰 오류·해제된 로봇 | 재발급 필요. 재시도 무의미 |
| 404 `PLANT_ASSIGNMENT_NOT_FOUND` | 배정된 식물 없음 | 앱에서 배정. 재시도 무의미 |
| 409 `PHOTO_ALREADY_EXISTS_FOR_DATE` | **그날 사진이 이미 있음** | 🔴 **정상 상황. 실패로 다루지 말 것** |
| 400 | 이미지로 안 읽힘 | 재촬영 |
| 413 | 크기 초과 | 품질 낮춰 재인코딩 |

서버는 타임랩스 프레임 간격을 일정하게 유지하려고 **하루 한 장만** 저장한다. 409 를 실패로
처리하면 자동 촬영 체인이 거기서 멈춘다.

### 업로드 토큰

로봇 등록 시 **한 번만** 응답에 나온다. 서버는 해시만 저장해 다시 조회할 수 없다.

- 분실 시 앱 **장치 관리 → 토큰 재발급**
- 기기를 해제하고 재등록하면 **토큰이 바뀐다**
- 코드나 git 에 넣지 않는다. 환경변수로 주입한다

### 회신

```
potner/device/raspberry-01/result/capture
{"messageId":"<uuid>","deviceId":"raspberry-01","requestId":"<그대로>",
 "status":"OK","measuredAt":"..."}
```

실패는 `ERROR` + `error` 문구 + `code`. 촬영 중 새 명령은 `BUSY`.

### 🔴 업로드 실패가 촬영 결과를 뒤집지 않게 한다

사진은 이미 로컬에 있어 나중에 다시 올릴 수 있다. 업로드 실패로 `result/capture` 를 `ERROR` 로
보내면 **서버의 자동 촬영 체인이 멈추고 그날 촬영을 다시 시도하지 않는다.**
`status` 는 `OK` 를 유지하고 업로드 결과는 별도 필드로 담는다.

---

## 8. 송풍 — `command/fan` 수신 → `result/fan` 회신 (🔴 미구현)

하드웨어 제어(`cli.fan`)는 이미 있고 **MQTT 리스너만 없다.** 급수 수신기와 같은 모양으로
붙이면 된다.

### 받는 것

```
potner/device/raspberry-01/command/fan
{"seconds": 30, "requestId": "<uuid>"}
```

**가동 시간을 서버가 정한다.** 말리기는 "짧은 가동 → 수분 재측정 → 필요하면 재가동" 의
반복이라 한 번에 오래 돌리지 않는다 — 과건조는 되돌릴 수 없다.

### 보내는 것

```
potner/device/raspberry-01/result/fan
{"messageId":"<uuid>","deviceId":"raspberry-01","requestId":"<그대로>",
 "status":"OK","measuredAt":"..."}
```

### 서버 쪽은 이미 다 붙어 있다

토양수분 **초과**(`HIGH`) 알림이 열리면 서버가 자동으로:

```
NAVIGATE(WATER_STATION) → FAN(반복) → 마르면 NAVIGATE(HOME)
```

- 알림이 닫히는 것이 정상 종료다
- 하루 가동 상한 6회 (수분 센서 고장으로 팬이 끝없이 도는 것을 막는 안전장치)
- 말리는 동안은 스테이션에 머물고 매 회차 왕복하지 않는다

**이 수신이 없으면 이동만 하고 송풍 단계에서 타임아웃으로 멈춘다.**

---

## 9. 스테이션 물 부족 보고 (🔴 미구현)

스테이션 수위 센서를 읽는 파이가 보낸다.

```
potner/device/raspberry-01/status/water-low
```

```json
{
  "messageId": "<uuid>",
  "deviceId": "raspberry-01",
  "waterLow": true,
  "measuredAt": "2026-08-03T02:15:00Z"
}
```

- **불리언이다.** 수위 퍼센트가 아니다. 임계값 판정은 센서를 아는 장치가 한다
- 🔴 **`waterLow: false` 도 보내야 한다.** 물을 보충하면 false 를 보내야 서버 플래그가 내려가고,
  그래야 다음 부족 때 알림이 다시 나간다
- 상태가 바뀔 때든 주기 보고든 상관없다 — 서버 처리가 멱등해서 같은 상태를 반복 보고해도
  알림이 쏟아지지 않는다
- 사용자 푸시는 **부족으로 바뀔 때 한 번만** 나간다

### 전제 조건

로봇에 급수 스테이션이 **등록되어 있어야 한다**(앱의 위치 설정). 없으면 서버 로그에
`the robot has no water station registered` 로 버려진다. 현재 `station-01` 로 등록되어 있다.

---

## 10. 안 될 때 — 로그 문구로 원인 찾기

MQTT 는 응답이 없는 단방향이라 **서버 로그가 유일한 진단 수단이다.**

```bash
docker logs --since 5m potner-infra-test-backend-1 2>&1 \
  | grep -iE "sensor reading|heartbeat|water|capture|fan|command result|alert"
```

검사는 순서대로 이루어지고 단계마다 다른 문구를 남긴다.

| 순서 | 검사 | 실패 시 로그 | 흔한 원인 |
|---|---|---|---|
| 1 | 토픽 정규식 | `Invalid MQTT sensor telemetry topic` | 토픽 오타, 접미사 |
| 2 | JSON 파싱·enum | `Invalid MQTT sensor telemetry JSON` | enum 대소문자, 깨진 JSON |
| 3 | 필수값·범위 | `Invalid MQTT sensor telemetry values: fields=...` | 누락 필드 |
| 4 | 타입↔단위↔범위 | `Invalid MQTT sensor telemetry type/unit/value` | 4절 표 불일치 |
| 5 | 미래 시각 | `Invalid MQTT sensor telemetry measuredAt` | NTP 미동기화 |
| 6 | 토픽 ↔ 페이로드 | `MQTT sensor telemetry deviceId mismatch` | 2절 비대칭 |
| 7 | 저장 | `IoT device was not found` | **앱에서 기기 등록 안 됨** |
| 7 | 저장 | `active plant assignment was not found` | 식물 배정 안 함 |
| 7 | 저장 | (아무것도 안 나옴) | **`messageId` 재사용** |
| — | 구독 토픽 아님 | `topic is unsupported` | 서버가 구독하지 않는 토픽 |

### 성공 로그

| 신호 | 로그 | 레벨 |
|---|---|---|
| 센서 측정값 | `MQTT sensor reading saved: messageId=..., sensorType=...` | INFO |
| 하트비트 | `MQTT heartbeat processed` | DEBUG — **기본 설정에서 안 보임** |
| 명령 회신 | `Device command result applied: requestId=..., status=..., dispensedMl=...` | INFO |
| 명령 발행 | `Robot command published: topic=..., payload=...` | INFO |

하트비트는 로그가 아니라 **결과를 봐야 한다** — `GET /api/v1/plants/{plantId}/devices` 의
`connectionStatus` 가 `ONLINE` 이면 도착하고 있는 것이다.

---

## 11. 남은 작업 정리

| 우선순위 | 작업 |
|---|---|
| 🔴 1 | **`Raspberry-develop` 을 파이에 배포** — master 기준이면 MQTT 연동이 통째로 없다 |
| 🔴 2 | **사진 업로드** (`POST /device/photos`) — 여섯 기능이 여기 걸려 있다 |
| 🔴 3 | **`pump.driver` 확인** — `mock` 이면 실제 펌프가 안 돈다 |
| 🟡 4 | **`command/fan` 수신** — 서버 자동 말리기가 이걸 기다린다 |
| 🟡 5 | **조도 전송 복구** — 하루 광량 판정의 유일한 입력 |
| ⚪ 6 | 스테이션 물 부족 보고 |
| ⚪ 7 | `flow_ml_per_sec` 실측 보정 |
