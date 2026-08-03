# GPS 전 귀가 마중 확인

가짜 Jetson 프로세스는 사용하지 않습니다. 운영 코드의 계약·상태 머신을 로봇 없이
검증하고, Jetson이 준비되면 같은 코드에서 Nav2만 우회해 앱-서버-MQTT 전체 통신을
확인합니다.

## 1. 로봇과 Jetson 없이

개발 PC의 Robot 저장소에서 실행합니다.

```bash
python -m pytest tests/test_arrival_contract.py \
  tests/test_arrival_session.py tests/test_arrival_flow.py -q
```

검증 범위:

- 서버 `welcome_start/cancel` JSON과 좌표·시간 검증
- GREETING 도착 시 start `OK`
- 사람 감지 또는 `waitSeconds` 만료 후 HOME 전환
- 같은 `visitId` 취소 시 HOME 도착 후 cancel `OK`
- QoS 1 중복, 다른 방문, BUSY, Nav2 오류 결과

## 2. Jetson은 있고 바퀴·Nav2가 없을 때

서버는 좌표가 없는 명령을 발행하지 않습니다. Swagger에서 해당 로봇에 `HOME`과
`GREETING` 위치를 등록하고, 통신 시험용으로 두 좌표를 모두
`{"x":0,"y":0,"yaw":0}`으로 넣습니다.

```text
POST /api/v1/robots/{robotId}/locations
{"type":"HOME","stationCode":null}

POST /api/v1/robots/{robotId}/locations
{"type":"GREETING","stationCode":null}

PUT /api/v1/robots/{robotId}/locations/HOME/pose
PUT /api/v1/robots/{robotId}/locations/GREETING/pose
{"x":0,"y":0,"yaw":0}
```

지도가 만들어지면 이 임시 좌표는 반드시 실제 RViz 좌표로 덮어씁니다.

운영 설정은 `skip_navigation: false`입니다. 통신 시험 동안만 바꿉니다.

```bash
ros2 param set /mission_manager skip_navigation true
ros2 topic echo /mission/arrival_command
ros2 topic echo /mission/arrival_result
```

그 상태에서 앱의 `마중 시작` 버튼을 누릅니다.

1. 서버 로그에 `welcome_start` 발행이 남습니다.
2. `/mission/arrival_command`에 GREETING/HOME 좌표가 보입니다.
3. 이동만 건너뛰고 `/mission/arrival_result`에 start `OK`가 보입니다.
4. 앱 상태가 `OK`로 바뀝니다.
5. `취소·HOME`을 누르면 cancel `OK`가 보입니다.

끝나면 반드시 실제 이동 설정으로 돌립니다.

```bash
ros2 param set /mission_manager skip_navigation false
```

## 3. 조립과 지도 완성 뒤

서버에 로봇의 `GREETING`과 `HOME` map 좌표를 먼저 등록합니다. Robot 설정의
정적 `greet_pose`는 쓰지 않습니다.

확인 순서는 다음과 같습니다.

1. 마중 시작 후 Nav2가 GREETING으로 이동
2. GREETING 도착 순간 서버와 앱이 `OK`
3. 사람을 보이면 한 번 인사하고 HOME 복귀
4. 사람이 없으면 기본 120초 뒤 HOME 복귀
5. 이동 중 취소하면 기존 목표를 취소하고 HOME으로 변경
6. HOME 도착 뒤 cancel `OK`
