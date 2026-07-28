# legacy — ROS 2 전환 이전 코드

**이 폴더의 코드는 더 이상 실행되지 않습니다.** 참고용으로만 남겨둡니다.

ROS 2로 옮기면서 하드웨어 구성이 바뀌었기 때문에 그대로는 동작하지
않습니다. 가장 큰 변화는 **애커만 조향(서보) → 차동 구동(좌우 모터 독립)**
입니다. `wheel_motor_controller.py` 의 조향 서보 제어는 목표 하드웨어에
존재하지 않는 부품을 다루고 있습니다.

## 어디로 갔나

| 이 폴더의 파일 | 이관처 | 비고 |
|---|---|---|
| `main.py` 우선순위 스케줄러 | `src/potner_mission/potner_mission/priority.py` | 로직 그대로 이식, 테스트 추가 |
| `main.py` 메인 루프 | `src/potner_mission/potner_mission/mission_manager_node.py` | 블로킹 → 비블로킹 상태 머신 |
| `vision/auto_docking_vision.py` solvePnP | `src/potner_perception/potner_perception/marker_pose.py` | 단위를 cm → m 로 변경 |
| 〃 P 제어 | `src/potner_docking/potner_docking/approach_controller.py` | 서보 각도 → 각속도(rad/s) |
| 〃 마커 탐지 | `src/potner_perception/potner_perception/marker_detector_node.py` | |
| 〃 YOLO 블록 | `src/potner_perception/potner_perception/person_detector_node.py` | 별도 프로세스로 분리 (GIL 회피) |
| 〃 시간 기반 회피 4단계 | **폐기** | Nav2 로컬 플래너가 대체 |
| `navigation/wheel_motor_controller.py` | `src/potner_base/` + `src/potner_firmware/` | 애커만 → 차동 구동 |
| `navigation/obstacle_detector.py` | `src/potner_mission/potner_mission/safety_node.py` | 초음파 → LiDAR + 범퍼 ToF |
| `sensors/ultrasonic.py` | **`Raspberry-master` 브랜치로 옮겨야 함** | 초음파는 스테이션 담당으로 결정됨 |
| `sensors/moisture.py` | `src/potner_base/` 계열로 재구현 예정 | ADS1115 경유로 바뀜 |
| `beacon/*.png` | `src/potner_docking/markers/` | 그대로 유지 |
| `test_straight_docking.py` | 필요시 `potner_base` 통합 테스트로 재작성 | 하드웨어 수동 테스트 스크립트 |
| `test_obstacle_avoidance.py` | 동일 | pytest 테스트가 아니라 수동 실행 스크립트였음 |

## 알아둘 것 — 이 코드는 젯슨에서 애초에 안 돌았습니다

`vision/auto_docking_vision.py` 와 `vision/create_station_markers.py` 는
`aruco.ArucoDetector` 를 씁니다. 이 API 는 **OpenCV 4.7 에서 추가**된
것인데, 젯팩 6 의 시스템 OpenCV 는 **4.5.4** 입니다. venv 안에 pip 로 깐
최신 OpenCV 에서만 동작했다는 뜻입니다.

새 코드(`potner_perception.marker_pose.create_detector`)는 양쪽 API 를
모두 지원하도록 만들었습니다. `cv_bridge` 가 시스템 OpenCV 4.5.4 에
링크되어 있어서 버전을 올리면 ROS 영상 스택이 깨지기 때문입니다.

## 왜 지우지 않고 남겨두나

1. **도킹 비전 알고리즘은 검증된 자산입니다.** solvePnP 파라미터와 P 제어
   게인의 원본 값을 대조할 곳이 필요합니다.
2. **하드웨어 초기화 순서**가 기록되어 있습니다. PCA9685를 100Hz로 패치한
   이유, I2C 주소(0x40/0x60) 같은 실기에서 얻은 정보는 문서에 없습니다.
3. 중간발표까지의 작업 기록입니다.

## 남은 정리 작업

- [ ] `sensors/ultrasonic.py` 를 `Raspberry-master` 브랜치로 옮기기
- [ ] 도킹 게인 재튜닝이 끝나면 이 폴더 전체 삭제 검토
