# MQTT — 로봇 쪽 구현 노트

> **메시지 형식의 기준은 [`DEVICE-MQTT.md`](DEVICE-MQTT.md) 입니다.** 서버 팀이
> 관리하고 이 문서보다 권위가 있습니다. 토픽 경로, 페이로드 필드, enum 값,
> 허용 범위는 모두 그쪽을 보세요.
>
> 예전에는 이 문서가 형식을 직접 정의했는데, 서버 문서가 나온 뒤로 두 문서가
> 서로 다른 말을 하기 시작했습니다. 어느 쪽을 믿을지 모르는 상황이 형식
> 불일치보다 위험하므로 여기서는 **로봇 쪽 결정만** 남깁니다.

관련 코드
- 메시지 생성: [`src/potner_bridge/potner_bridge/telemetry.py`](../src/potner_bridge/potner_bridge/telemetry.py)
- 귀가 계약: [`src/potner_bridge/potner_bridge/arrival_contract.py`](../src/potner_bridge/potner_bridge/arrival_contract.py)
- 노드: [`src/potner_bridge/potner_bridge/mqtt_bridge_node.py`](../src/potner_bridge/potner_bridge/mqtt_bridge_node.py)
- 귀가 상태 머신: [`src/potner_mission/potner_mission/arrival_session.py`](../src/potner_mission/potner_mission/arrival_session.py)
- 검증: [`tests/test_telemetry.py`](../tests/test_telemetry.py),
  [`tests/test_arrival_contract.py`](../tests/test_arrival_contract.py),
  [`tests/test_arrival_session.py`](../tests/test_arrival_session.py)

---

## 로봇이 주고받는 것

| 방향 | 토픽 | 주기 | ROS 원본 |
|---|---|---|---|
| 발행 | `sensor/telemetry` (`SOIL_MOISTURE`) | 10초 | `plant/moisture` |
| 발행 | `sensor/telemetry` (`ILLUMINANCE`) | 10초 | `plant/lux` |
| 발행 | `status/heartbeat` | 30초 | — |
| 발행 | `status/state` | 변화 시 + 30초 | `mission/state` |
| 발행 | `status/battery` | 60초 | `battery/percent` |
| 구독 | `command/expression` | 서버 발행 시 | `display/expression` |
| 구독 | `command/welcome_start` | 귀가 접근 시 | `mission/arrival_command` |
| 구독 | `command/welcome_cancel` | 귀가 취소 시 | `mission/arrival_command` |
| 발행 | `result/welcome_start` | GREETING 도착/실패 | `mission/arrival_result` |
| 발행 | `result/welcome_cancel` | HOME 도착/실패 | `mission/arrival_result` |

온도와 습도는 스테이션이 재서 서버로 직접 올립니다. 젯슨에서 DHT11 을 읽으려면
마이크로초 타이밍이 필요한데 리눅스는 실시간 OS 가 아니라 자주 실패합니다.

## 접속

```
호스트   i15e104.p.ssafy.io
포트     1884            ← 1883 이 아닙니다
계정명   jetson-01       ← device_id 와 반드시 같아야 합니다
비밀번호 POTNER_MQTT_PASSWORD 환경변수
client_id jetson-01-bridge
QoS      1
```

**계정명이 `device_id` 와 같아야 하는 이유는 ACL 이 `%u` 치환을 쓰기
때문입니다.** 다르게 두면 자기 토픽에 대한 권한이 없어져 발행이 전부 거부되고,
거부는 발행 쪽에 에러로 돌아오지 않아 "연결됨" 만 보이는 채로 값이 안
들어옵니다.

비밀번호는 저장소에 두지 않습니다. `potner_params.yaml` 에는 환경변수 **이름**만
적혀 있습니다.

## ACL 이 막는 것

```
pattern write potner/device/%u/sensor|status|result/#
pattern read  potner/device/%u/command/#
```

자기 `device/jetson-01/` 아래만 오갈 수 있습니다. 그래서 아래 설계는 **브로커
레벨에서 불가능**하고, 코드에서 걷어냈습니다.

| 걷어낸 것 | 원래 용도 | 남은 결과 |
|---|---|---|
| `potner/station/{역할}/request` 발행 | 급수·송풍 요청 | 급수 명령은 서버가 스테이션에 직접 보냅니다 |
| `potner/station/+/docked` 구독 | 홀 센서 접점 확인 | `require_station_confirm` 을 쓸 수 없습니다 (기본 `false`) |
| `potner/device/+/sensor/telemetry` 구독 | 스테이션 온도 받기 | `plant/temperature` 가 항상 `None` → **송풍 임무가 걸리지 않습니다** |
| `potner/device/{id}/speech` 구독 | LLM 대사 받기 | 서버 경유 대신 **음성 서버를 로봇에서 직접 띄워** 우회했습니다 (아래) |

## 배터리를 한 종류만 재는 이유

로봇에는 팩이 두 개입니다 — 4S 젯슨팩과 3S 모터팩. 그중 **젯슨팩만**
측정합니다.

젯슨은 15~25W 를 계속 먹는 반면 모터는 이동할 때만 돕니다. 용량도 젯슨팩
51.8Wh, 모터팩 38.9Wh 로 비슷해서 실사용에서는 **젯슨팩이 먼저 바닥납니다.**
먼저 떨어지는 쪽을 재면 충전 임무가 제때 걸리므로 모터팩까지 계측할 필요가
없습니다. INA226 도 한 개뿐입니다.

대신 **모터팩 고갈은 감지되지 않습니다.** 예상과 달리 모터팩이 먼저 떨어지면
로봇이 이유 없이 멈춘 것처럼 보입니다. 실주행에서 두 팩의 소모 속도를 한 번
확인해두면 좋습니다.

**잔량은 전압에서 환산한 추정치입니다.** 주행 중에는 부하 때문에 전압이 처져
실제보다 낮게 나옵니다. 충전 시점 판단에는 충분하지만 정밀한 잔량계로 쓰지
마세요.

## 범위를 벗어난 값을 깎지 않는 이유

서버는 허용 범위를 벗어난 값을 **버립니다** (0 이나 100 으로 깎지 않습니다).
코드도 같은 방침이라 `telemetry.py` 가 예외를 내고 노드가 로그로 남깁니다.

깎으면 센서 고장이 정상값으로 위장됩니다. 값을 만드는 쪽의 버그(단위 착각,
셀 수 잘못 설정, 부호 반전)가 로그에 드러나야 고칠 수 있습니다.

> ⚠️ **이 검사가 못 잡는 경우가 있습니다.** `plant_conversions.battery_percent`
> 는 리튬이온 곡선의 양 끝에서 값을 잘라 항상 0~100 을 돌려줍니다. 그래서
> INA226 의 VBS 배선이 빠져 0V 를 읽으면 0% 가 되어 범위 검사를 그냥
> 통과하고, 앱에는 "배터리 없음" 으로 보입니다.
>
> 이걸 구분하려면 잔량이 아니라 **전압**을 봐야 합니다. 4S 팩이 살아 있으면
> BMS 컷오프 때문에 최소 12V 는 나오므로, 그보다 훨씬 낮은 전압은 배선
> 문제입니다. `plant_conversions.battery_wiring_suspect()` 가 이 판단을
> 하고, `plant_sensors_node` 가 감지되면 경고 로그를 남깁니다(값 발행은
> 그대로 합니다 — mission_manager 에게는 여전히 유효한 값이라서).

## 시각

전부 UTC 로 보냅니다. 한국 시각을 그대로 보내면 9시간 어긋난 기록이 쌓여
일기 생성과 성장 기록의 순서가 뒤섞입니다.

**서버는 현재보다 10분 이상 미래인 시각을 버립니다.** 젯슨에 RTC 배터리가
없어서 부팅 직후 시계가 틀어져 있을 수 있으므로 NTP 동기화가 필요합니다.

```
timedatectl status        # System clock synchronized: yes 확인
```

## 귀가 마중 동작

서버가 `GREETING`과 `HOME`의 map 좌표를 명령에 넣어 보내며 로봇의 정적
`greet_pose`는 사용하지 않습니다.

```
welcome_start
  -> GREETING Nav2 이동
  -> 도착하면 result/welcome_start OK
  -> 사람 인식 시 인사, 없으면 waitSeconds 대기
  -> HOME Nav2 복귀

welcome_cancel
  -> 진행 중 이동·대기 취소
  -> HOME Nav2 복귀
  -> 도착하면 result/welcome_cancel OK
```

QoS 1로 같은 명령이 다시 와도 `requestId` 결과 캐시로 이동을 중복 실행하지
않습니다. 다른 `visitId`의 명령이나 다른 임무 중 새 시작은 `BUSY`, Nav2 실패는
`ERROR`로 회신합니다. Jetson은 자체 임계값 판단을 하지 않습니다 — 이동의
트리거는 서버 명령뿐이라 둘이 동시에 로봇을 움직일 일이 없습니다.

`command/navigate`도 같은 모양입니다. 자세한 계약과 확인 절차는
[`NAVIGATE_TEST.md`](NAVIGATE_TEST.md)에 있습니다.

## 음성 대화는 MQTT를 쓰지 않습니다

브로커 ACL이 장치에 `command/#` 읽기만 허용해서 서버→장치 대사 토픽을 만들 수
없었습니다. `command/speech` 추가를 기다리는 대신, **음성 서버를 로봇에서 직접
띄우는** 쪽으로 닫았습니다. 서버 팀 작업이 필요 없습니다.

```
폰 브라우저 ──(같은 Wi-Fi)──> voice-chat-server (로봇 온보드 :8080)
                                │ GMS STT → LLM → GMS TTS(wav)
                                │ 조각을 스풀에 쓰고 경로 발행
                                v
                             tts/play_audio ──> speaker_node ──> 스피커
```

`tts/play_audio` / `tts/cancel`은 **로봇 내부 ROS 토픽**이라 브로커와 무관합니다.
실행법과 계약은 [`../voice-chat-server/README.md`](../voice-chat-server/README.md).

## 아직 미구현

- **`command/speech`** — 서버가 로봇에게 대사를 내려보내는 경로. 위 우회로
  당장 급하지 않습니다. 서버가 대화를 주도해야 할 일이 생기면 요청하세요
