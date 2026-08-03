# 서버 이동 명령(`command/navigate`) 확인

자동 급수·자동 말리기·자동 촬영·햇빛 이동 네 기능이 전부 이 명령으로 시작합니다.
로봇은 받은 좌표로 가서 "도착했다"만 회신하고, 그 회신이 와야 서버가 다음 단계를
발행합니다.

계약 원문은 서버 저장소 `docs/DEVICE-MQTT.md` 16절입니다.
(Robot 저장소의 사본은 15절까지라 이 명령이 없습니다.)

```
서버 → 젯슨   potner/device/<uid>/command/navigate
{"destination":"WATER_STATION","x":1.250,"y":-0.480,"yaw":1.5708,"requestId":"<uuid>"}

젯슨 → 서버   potner/device/<uid>/result/navigate
{"messageId":"<새 uuid>","deviceId":"<uid>","requestId":"<그대로 반향>","status":"OK"}
```

`OK`는 출발이 아니라 **도착(도킹 완료)** 입니다.

## 0. 반드시 알아야 하는 것 하나

**같은 `requestId`가 두 번 와도 `BUSY`를 보내지 않습니다.** 서버는 OK/ERROR/BUSY를
모두 종단 상태로 봅니다(`DeviceCommandStatus.isTerminal`). QoS 1 재전송에 BUSY를
회신하면 서버가 수행 중인 명령을 실패로 확정하고, 자동 케어 체인이 거기서 멈추며,
진짜 도착해서 보낸 OK는 `ALREADY_COMPLETED`로 버려집니다. 로봇은 스테이션 앞에
서 있는데 물은 나오지 않습니다.

| 상황 | 회신 |
|---|---|
| 같은 requestId, 수행 중 | **없음.** 도착하면 한 번만 |
| 같은 requestId, 완료됨 | 그때 결과를 다시 |
| **다른** requestId, 수행 중 | `BUSY` — 여기서만 |

## 1. 로봇과 Jetson 없이

개발 PC의 Robot 저장소에서 실행합니다.

```bash
python -m pytest tests/test_navigate_contract.py \
  tests/test_navigate_session.py tests/test_navigate_flow.py -q
```

검증 범위:

- 서버 이동 JSON 파싱, 목적지 4종, 좌표 검증, 지도 원점(0,0,0) 허용
- `requestId` 반향과 회신마다 새 `messageId`
- 회신 토픽이 `result/` 아래인지
- QoS 1 재전송에 회신하지 않음, 완료된 명령은 결과 재전송
- 이동 중 다른 명령은 `BUSY`, 도킹 실패는 `ERROR` + 사유
- 급수 스테이션 파킹 중 마중 명령 거절

## 2. Jetson은 있고 바퀴·Nav2·카메라가 없을 때

`skip_navigation`은 Nav2만 건너뛰고 도킹은 그대로 합니다(도킹만 따로 시험하려고
그렇게 만들어져 있습니다). 카메라가 없으면 `WATER_STATION` 이동이 도킹에서
멈추고, 실패 경로도 만들 수 없습니다. 목 액션 서버를 씁니다.

```bash
# 터미널 A — Nav2와 도킹 서버를 흉내 냅니다. 진짜와 같이 띄우지 마세요.
python3 tools/mock_nav_actions.py --ros-args -p duration:=3.0

# 터미널 B
ros2 run potner_mission mission_manager --ros-args --params-file <params>
POTNER_MQTT_PASSWORD='...' ros2 run potner_bridge mqtt_bridge --ros-args --params-file <params>

# 터미널 C
ros2 topic echo /mission/navigate_command
ros2 topic echo /mission/navigate_result
```

서버가 좌표 없는 위치로는 명령을 발행하지 않으므로 Swagger에서 위치를 먼저
등록합니다. 통신 시험 동안에는 임시 좌표로 둡니다.

```text
POST /api/v1/robots/{robotId}/locations       {"type":"WATER_STATION","stationCode":"<코드>"}
POST /api/v1/robots/{robotId}/locations       {"type":"HOME","stationCode":null}
PUT  /api/v1/robots/{robotId}/locations/WATER_STATION/pose   {"x":0,"y":0,"yaw":0}
PUT  /api/v1/robots/{robotId}/locations/HOME/pose            {"x":0,"y":0,"yaw":0}
```

지도가 만들어지면 이 임시 좌표는 반드시 실제 RViz 좌표로 덮어씁니다.

확인 순서:

1. 앱이나 Swagger로 `NAVIGATE`(`WATER_STATION`)를 발행
2. `/mission/navigate_command`에 서버 좌표가 보임
3. 3초 뒤 `/mission/navigate_result`에 `OK`
4. 서버 로그 `Device command result applied: ... type=NAVIGATE, status=OK`
5. 이어서 서버가 라즈베리에 `WATER`를 발행

실패 경로와 중복 수신도 여기서 만들 수 있습니다.

```bash
# 도킹 실패 -> ERROR + DOCKING_FAILED
python3 tools/mock_nav_actions.py --ros-args -p dock_outcome:=abort

# 이동 실패 -> ERROR + NAVIGATION_FAILED
python3 tools/mock_nav_actions.py --ros-args -p nav_outcome:=abort

# 액션이 응답하지 않음 -> navigate_timeout 뒤 ERROR + NAVIGATION_TIMEOUT
python3 tools/mock_nav_actions.py --ros-args -p nav_outcome:=hang

# 중복 수신 — 같은 requestId 를 손으로 두 번 보냅니다.
# 두 번째에는 아무 회신도 나가지 않아야 합니다.
mosquitto_pub -h i15e104.p.ssafy.io -p 1884 -u <uid> -P "$POTNER_MQTT_PASSWORD" \
  -i <uid>-manual -t 'potner/device/<uid>/command/navigate' \
  -m '{"destination":"HOME","x":0,"y":0,"yaw":0,"requestId":"11111111-1111-4111-8111-111111111111"}'
```

## 3. 조립과 지도 완성 뒤

서버에 실제 map 좌표를 먼저 등록합니다. 로봇 설정에는 목적지 좌표가 없습니다 —
출처는 서버의 `robot_location` 하나입니다.

확인 순서:

1. `WATER_STATION` 이동 → Nav2가 근처까지, 마커 2번으로 정밀 도킹
2. 도킹 완료 순간 `OK` → 서버가 라즈베리에 급수 명령
3. 급수 중 앱에서 `마중 시작` → `BUSY` + `SERVICING_AT_STATION` (로봇이 떠나지 않음)
4. 급수 완료 → 서버가 `NAVIGATE`(`HOME`) → 도킹 없이 도착 `OK`
5. HOME 도착 뒤 마중 명령이 정상으로 받아짐

## 시간 제약 — 실측이 필요한 지점

서버 제한시간은 **120초**입니다(`DEVICE_COMMAND_TIMEOUT_SECONDS`, 판정 주기 30초라
실제 컷오프는 120~150초). `docking_timeout` 기본값이 90초라 Nav2 주행 시간을 더하면
넘길 수 있습니다.

넘겨도 체인은 삽니다 — `TIMED_OUT`은 종단 상태가 아니라 늦은 회신이 그 위에
덮어쓰고 다음 단계가 이어집니다. 다만 명령 이력에 `TIMED_OUT`이 남습니다.

실측해서 자주 넘긴다면 서버 팀에 **NAVIGATE 전용 타임아웃**을 요청하세요. 지금은
급수·촬영·송풍·이동이 값 하나를 공유합니다.
