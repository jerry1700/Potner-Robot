# Potner — 반려식물 AIoT 로봇 (Robot 파트)

> Pot + Partner. 스스로 판단하고 움직이며 사용자와 교감하는 반려식물 로봇.
> SSAFY 15기 공통PJT · E104 친구사이

이 저장소의 `Robot-master` 브랜치는 **로봇 본체(Jetson Orin Nano)** 소프트웨어를
담당합니다. 다른 파트는 별도 브랜치에 있습니다 — `App-master`(Flutter),
`Server-master`(Spring Boot), `Raspberry-master`(장치 스테이션).

## 시스템 구성

```
① Jetson Orin Nano — 판단
   ├ USB ─ LiDAR (YDLIDAR X4 Pro), 카메라 (BRIO 100), 스피커
   ├ HDMI ─ 7인치 LCD (표정)
   ├ I2C ─ BH1750 조도, ADS1115 ─ 정전식 토양 수분
   └ USB ─────┐
              │
② ESP32 — 모터 (실시간)
   ├ PWM 20kHz ─ BTS7960 #1 ═ 좌 구동모터 (FIT0403)
   ├ PWM 20kHz ─ BTS7960 #2 ═ 우 구동모터 (FIT0403)
   └ 레벨시프터 ─ 엔코더 A/B ×2

③ 전원 — 분리
   4S 배터리 ─ Jetson
   3S 배터리 ─ 15A 지연형 퓨즈 ─ XT60 ─ BTS7960 ═ 모터
   ⚠️ 두 팩의 GND는 반드시 한 가닥으로 연결 (플러스는 절대 금지)
```

**역할을 나눈 이유** — 엔코더는 초당 약 2,930카운트 × 2개가 들어옵니다.
리눅스는 실시간 OS가 아니라 파이썬으로 이걸 세면 카운트를 흘리고, 그러면
오도메트리가 틀어져 Nav2와 AMCL이 통째로 무너집니다. 그래서 실시간이
필요한 일(엔코더, PID, PWM)은 ESP32가, 판단이 필요한 일(경로계획, 비전,
서버 연동)은 젯슨이 맡습니다.

## 디렉토리 구조

이 저장소의 루트가 곧 **colcon 워크스페이스**입니다.

```
src/
├── potner_base/          젯슨 ↔ ESP32 통신, 차동 구동 기구학, 오도메트리
│   └── kinematics.py         ★ 순수 로직 (CI에서 테스트)
│   └── serial_protocol.py    ★ 순수 로직 (CI에서 테스트)
├── potner_description/   URDF 형상 정의, RViz 설정
├── potner_bringup/       launch 파일과 전체 파라미터
├── potner_perception/    YOLO 사람 인식, ArUco 마커 탐지
├── potner_docking/       스테이션 정밀 도킹 (마커 기반 P 제어)
├── potner_mission/       임무 상태 머신, 최우선 안전 정지
├── potner_bridge/        MQTT ↔ ROS 2 브리지 (서버·스테이션 연동)
├── potner_msgs/          커스텀 메시지·액션 정의
└── potner_firmware/      ESP32 펌웨어 (PlatformIO, ROS 패키지 아님)

tests/                    CI용 단위 테스트 (ROS 없이 순수 로직만)
legacy/                   ROS 2 전환 이전 코드 (참고용, 실행되지 않음)
```

**노드 파일과 계산 로직을 파일 단위로 분리**한 것이 설계의 핵심입니다.
`kinematics.py` 같은 순수 모듈은 rclpy를 import 하지 않아서 젯슨 없이도
노트북과 CI 서버에서 검증할 수 있습니다.

## 시작하기

### 1. 사전 요구사항

- Jetson Orin Nano, JetPack 6 (Ubuntu 22.04 jammy)
- ROS 2 Humble — [공식 설치 문서](https://docs.ros.org/en/humble/Installation/Ubuntu-Install-Debians.html)

```bash
sudo apt install -y ros-dev-tools ros-humble-navigation2 ros-humble-nav2-bringup \
  ros-humble-slam-toolbox ros-humble-twist-mux ros-humble-robot-state-publisher \
  ros-humble-xacro ros-humble-v4l2-camera ros-humble-tf2-tools
```

`~/.bashrc` 에 아래를 넣으세요. `ROS_DOMAIN_ID` 를 팀 번호로 고정하지
않으면 **교육장의 다른 팀 ROS 기기와 토픽이 섞입니다.**

```bash
source /opt/ros/humble/setup.bash
export ROS_DOMAIN_ID=104
```

### 2. 워크스페이스 빌드

```bash
git clone <이 저장소> ~/potner_ws
cd ~/potner_ws
rosdep install --from-paths src --ignore-src -r -y
colcon build --symlink-install
source install/setup.bash
```

### 3. USB 장치 이름 고정 (중요)

LiDAR와 ESP32가 **둘 다 `/dev/ttyUSB*`** 로 잡힙니다. 꽂는 순서나 부팅
타이밍에 따라 번호가 바뀌어서, 어느 날 갑자기 LiDAR 자리에 모터 명령이
날아갑니다. 시리얼 번호로 이름을 고정하세요.

```bash
# 먼저 각 장치의 정보를 확인
udevadm info -a -n /dev/ttyUSB0 | grep -E 'idVendor|idProduct|serial' | head -5
```

`/etc/udev/rules.d/99-potner.rules` 를 만들고:

```
SUBSYSTEM=="tty", ATTRS{idVendor}=="10c4", ATTRS{serial}=="<ESP32 시리얼>", SYMLINK+="ttyUSB_ESP32"
SUBSYSTEM=="tty", ATTRS{idVendor}=="10c4", ATTRS{serial}=="<LiDAR 시리얼>", SYMLINK+="ttyUSB_LIDAR"
```

```bash
sudo udevadm control --reload-rules && sudo udevadm trigger
```

### 4. 단계별로 띄우기

한 번에 다 켜면 무엇이 고장났는지 알 수 없습니다. 순서대로 확인하세요.

```bash
# ① 형상 확인 — 로봇 없이 노트북에서도 됩니다
ros2 launch potner_description view_model.launch.py

# ② 로봇 본체 (센서 + 모터 + 안전)
ros2 launch potner_bringup robot.launch.py

# ③ 지도 만들기 — 키보드로 천천히 몰면서 집을 한 바퀴
ros2 launch potner_bringup slam.launch.py
ros2 run teleop_twist_keyboard teleop_twist_keyboard \
    --ros-args -r cmd_vel:=cmd_vel_teleop
ros2 run nav2_map_server map_saver_cli -f ~/potner_ws/maps/home

# ④ 자율주행
ros2 launch potner_bringup nav2.launch.py map:=$HOME/potner_ws/maps/home.yaml
```

### 5. ESP32 펌웨어

`src/potner_firmware/README.md` 참고. **바퀴를 공중에 띄운 채로** 첫
동작을 확인하세요.

## 주행 명령의 흐름

여러 노드가 동시에 모터를 건드리지 못하도록 `twist_mux` 가 중재합니다.
우선순위가 높은 입력이 낮은 쪽을 자동으로 차단합니다.

| 우선순위 | 토픽 | 발행 노드 |
|---|---|---|
| 255 | `cmd_vel_safety` | `safety` — LiDAR·범퍼 감지 시 정지 |
| 200 | `cmd_vel_teleop` | 사람이 직접 조종 |
| 150 | `cmd_vel_docking` | `docking_server` — 스테이션 정밀 접근 |
| 100 | `cmd_vel_nav` | Nav2 자율주행 |

최종 `/cmd_vel` 만 `base_driver` 가 받아 ESP32로 내려보냅니다. 긴급정지
플래그를 손으로 관리하지 않는 이유가 이것입니다.

## 조립 후 반드시 채워야 할 값

`src/potner_bringup/config/potner_params.yaml` 과
`src/potner_description/urdf/potner.urdf.xacro` 에 `TODO` 로 표시해 두었습니다.

| 값 | 왜 중요한가 |
|---|---|
| `wheel_diameter` | 틀리면 로봇이 지도에서 흘러갑니다 |
| `wheel_separation` | 틀리면 회전량이 어긋나 지도가 뒤틀립니다 |
| `lidar_height` | 폴 위 LiDAR의 높이. 스캔이 엉뚱한 곳에 찍힙니다 |
| `marker_size` | 인쇄한 ArUco 마커 실측. 도킹 거리 오차의 원인 |
| `focal_length_px` | `cv2.calibrateCamera` 로 BRIO 100 캘리브레이션 |

`counts_per_rev: 1440` 은 FIT0403 사양에서 나온 **확정값**이니 건드리지 마세요.

## 테스트

```bash
pytest tests/ -v
```

ROS 2 없이 돌아갑니다. 젠킨스 CI가 이 테스트와 flake8 문법 검사를
자동으로 실행합니다 ([Jenkinsfile](Jenkinsfile)).

## 다음 작업

- [ ] ESP32 펌웨어 실기 검증 및 PID 튜닝
- [ ] YDLIDAR 드라이버 소스 빌드 후 `robot.launch.py` 주석 해제
- [ ] `potner_msgs` 빌드 후 `DockToStation` 액션 서버 연결
- [ ] 스테이션별 지도 좌표 등록 (`mission_manager` 의 `_start_mission`)
- [ ] YOLO TensorRT 변환 (`yolo export format=engine`)
- [ ] `legacy/sensors/ultrasonic.py` 를 `Raspberry-master` 로 이관
