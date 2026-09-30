<div align="center">

# Potner

### 반려식물 AIoT 자율주행 로봇

<img src="docs/media/potner.png" width="320" alt="포트너 — 반려식물 로봇">

![ROS 2](https://img.shields.io/badge/ROS_2-Humble-22314E?logo=ros&logoColor=white)
![Jetson](https://img.shields.io/badge/NVIDIA-Jetson_Orin_Nano-76B900?logo=nvidia&logoColor=white)
![Python](https://img.shields.io/badge/Python-3.10-3776AB?logo=python&logoColor=white)
![ESP32](https://img.shields.io/badge/ESP32-Motor_Control-E7352C?logo=espressif&logoColor=white)
![MQTT](https://img.shields.io/badge/MQTT-Device_Bridge-660066?logo=mqtt&logoColor=white)
![SSAFY](https://img.shields.io/badge/SSAFY-공통_프로젝트-1428A0)

`SLAM 자율주행` · `ArUco 정밀 도킹` · `자동 급수·송풍` · `안전 정지` · `음성 대화`

</div>

---

**Pot + Partner.** 포트너는 화분을 싣고 이동하며 식물의 상태에 맞춰 급수·송풍·일광욕·촬영 임무를 수행하고, 사용자와 음성으로 교감하는 반려식물 로봇입니다.

SSAFY 15기 공통 프로젝트 · E104 친구사이 · 2026.07 ~ 08  
이 저장소는 전체 서비스 중 **Jetson Orin Nano 기반 로봇 본체와 ESP32 모터 제어 영역**을 담습니다. 앱(Flutter), 서버(Spring Boot), 장치 스테이션(Raspberry Pi)은 팀 GitLab에서 별도로 관리했습니다.

## 프로젝트 한눈에 보기

| 구분 | 내용 |
|---|---|
| 해결하려는 문제 | 사람이 계속 확인하지 않아도 식물 상태에 맞는 관리 행동을 수행하는 이동형 반려식물 시스템 |
| 핵심 동작 | 서버 임무 판단 → MQTT 명령 → Nav2 자율주행 → ArUco 정밀 도킹 → 급수·송풍·촬영 → 대기 장소 복귀 |
| 로봇 플랫폼 | Jetson Orin Nano + ROS 2 Humble + ESP32 차동 구동 |
| 인식·주행 | YDLIDAR X4 Pro, SLAM Toolbox, Nav2, YOLO, ArUco |
| 안전 설계 | LiDAR·범퍼 정지가 수동 조종·도킹·자율주행보다 항상 높은 우선순위를 갖도록 중재 |
| 통신 | MQTT로 서버·스테이션과 연동하고 USB Serial로 Jetson과 ESP32 연결 |

## 무엇을 하는 로봇인가

서버는 센서값을 바탕으로 **무엇을 할지** 결정하고, 로봇은 명령을 받아 **어떻게 안전하게 수행할지** 책임집니다.

- **자율 급수·송풍** — 현재 위치에서 장치 스테이션까지 이동하고 ArUco 마커로 정밀 도킹한 뒤, 스테이션이 필요한 관리를 수행하면 대기 장소로 복귀합니다.
- **생육 기록** — 매일 스테이션에 도킹해 식물 사진을 촬영하고 성장 기록을 남깁니다.
- **일광욕·귀가 마중** — 서버가 전달한 좌표로 이동해 창가에서 햇빛을 받거나 현관에서 사용자를 맞이합니다.
- **음성 대화와 표정** — STT→LLM→TTS 파이프라인으로 식물 페르소나가 답하고, 7인치 화면에 상태별 표정을 표시합니다.
- **안전 정지** — LiDAR와 범퍼 감지를 모든 주행 명령보다 우선해 충돌 위험이 생기면 즉시 정지합니다.

**목차** — [완성 모습](#완성-모습-2026-08-10-시연) · [시연 영상](#시연-영상) ·
[시스템 구성](#시스템-구성) · [핵심 설계와 근거](#핵심-설계와-근거) ·
[트러블슈팅](#트러블슈팅) · [디렉토리 구조](#디렉토리-구조) ·
[시작하기](#시작하기) · [주행 명령의 흐름](#주행-명령의-흐름) · [테스트](#테스트)

## 완성 모습 (2026-08-10 시연)

| 전체 모습 — 라이다 기둥과 화분 | 스테이션 도킹 |
|:---:|:---:|
| <img src="docs/media/potner-full.png" width="280" alt="로봇 전체 모습"> | <img src="docs/media/docking.png" width="360" alt="스테이션 도킹 장면"> |
| **장치 스테이션 — 펌프·카메라·ArUco 마커** | **시연장 전경** |
| <img src="docs/media/station.png" width="360" alt="장치 스테이션"> | <img src="docs/media/scene.jpg" width="400" alt="시연장에서 스테이션으로 이동하는 모습"> |

### 시연 영상

썸네일을 누르면 영상 파일(MP4)이 열립니다.

| 자동 급수 전체 시나리오 (1분 12초) | 스테이션 자동 도킹과 급수 (56초) |
|:---:|:---:|
| <a href="docs/media/watering-scenario.mp4"><img src="docs/media/watering-scenario-thumb.jpg" width="360" alt="자동 급수 전체 시나리오 영상"></a> | <a href="docs/media/docking-watering.mp4"><img src="docs/media/docking-watering-thumb.jpg" width="360" alt="스테이션 자동 도킹과 급수 영상"></a> |
| 물이 부족하면 임의의 위치에서 스테이션을 찾아가<br>필요한 행동을 마치고 대기 장소로 복귀<br>[▶ 영상 보기](docs/media/watering-scenario.mp4) | 마커 도킹부터 급수까지 가까이에서 본 장면<br>[▶ 영상 보기](docs/media/docking-watering.mp4) |

## 시스템 구성

| 계층 | 위치 | 역할 |
|---|---|---|
| 서버 (Spring Boot) | 서버 파트 | 센서값을 보고 임무를 결정·스케줄링 — 급수·송풍·촬영·이동 명령을 MQTT로 내림 |
| **Jetson Orin Nano (ROS 2)** | **이 저장소** | 인식(YOLO·ArUco), 경로 계획(Nav2), 정밀 도킹, 안전 정지, 서버 연동 |
| **ESP32** | **이 저장소** (`src/potner_firmware`) | 엔코더·PID·PWM — 실시간 모터 제어 |
| 장치 스테이션 (Raspberry Pi) | 스테이션 파트 | 도킹한 로봇의 화분에 급수·송풍, 식물 사진 촬영 |

```
① Jetson Orin Nano — 인식·경로 계획·서버 연동 (ROS 2)
   ├ USB ─ LiDAR (YDLIDAR X4 Pro), 카메라 (BRIO 100), 스피커
   ├ DP ─ 액티브 어댑터 ─ 7인치 LCD 1024x600 (표정)
   │      ⚠️ 젯슨에 HDMI 단자가 없습니다. 패시브 어댑터는 동작하지
   │         않습니다 → docs/JETSON_DISPLAY.md
   ├ I2C ─ BH1750 조도, ADS1115 ─ 정전식 토양 수분
   └ USB ─────┐
              │
② ESP32 — 모터 (실시간)
   ├ PWM 20kHz ─ BTS7960 #1 ═ 좌 구동모터 (FIT0403)
   ├ PWM 20kHz ─ BTS7960 #2 ═ 우 구동모터 (FIT0403)
   └ 레벨시프터 ─ 엔코더 A/B ×2

③ 전원 — 분리
   4S 배터리 ─ Jetson
   3S 배터리(BMS) ─ 20A 지연형 퓨즈 ─ XT60 ─ BTS7960 ═ 모터
   ⚠️ 두 팩의 GND는 반드시 한 가닥으로 연결 (플러스는 절대 금지)
```

**젯슨과 ESP32를 나눈 이유** — 주행 속도 상한(0.25m/s)에서도 엔코더는 초당 약 7,600카운트 × 2개가 들어옵니다.
리눅스는 실시간 OS가 아니라 파이썬으로 이걸 세면 카운트를 흘리고, 그러면
오도메트리가 틀어져 Nav2와 AMCL이 통째로 무너집니다. 그래서 실시간이
필요한 일(엔코더, PID, PWM)은 ESP32가, 계산이 무거운 일(경로 계획, 비전,
서버 연동)은 젯슨이 맡습니다.

## 핵심 설계와 근거

- **Jetson과 ESP32의 역할 분리** — 주행 속도 상한(0.25m/s)에서 엔코더 두 개로 초당 약 15,000카운트가 들어오기 때문에 비실시간 OS인 Linux에서 직접 계수하면 오도메트리가 흔들릴 수 있습니다. 엔코더·PID·PWM은 ESP32가 담당하고, Jetson은 Nav2 경로 계획·비전·서버 연동에 집중하도록 나눴습니다.
- **두 단계 자율주행** — 장거리 이동에는 SLAM 지도와 Nav2 경로를 사용할 수 있고, 스테이션 근처에서는 ArUco 마커 기반 P 제어로 전환해 최종 접근 오차를 줄입니다. 시연(2026-08-10)은 가벽으로 만든 좁은 공간이라 Nav2 대신 오도메트리 좌표 주행(`use_simple_nav`, `NavigateToPose` 호환)과 ArUco 도킹으로 진행했습니다. 현재도 간이 주행이 기본이며 Nav2는 `use_simple_nav:=false`로 전환해 사용합니다.
- **명령 우선순위 단일화** — 여러 노드가 모터에 직접 명령하지 않고 `twist_mux`를 거치게 했습니다. 안전 정지(255) → 수동 조종(200) → 도킹(150) → 자율주행(100) 순서로 충돌을 방지합니다.
- **재부팅에도 유지되는 장치 경로** — LiDAR·ESP32·카메라의 `/dev/*` 번호가 바뀌는 문제를 udev 규칙과 고정 심볼릭 링크로 해결했습니다.
- **계약 중심 연동** — MQTT 토픽과 payload를 문서화하고 로봇·서버·스테이션이 같은 계약을 사용하도록 해 각 파트가 독립적으로 개발하고 통합할 수 있게 했습니다.

## 트러블슈팅

1. **오도메트리가 계속 틀어지는 문제** — Jetson에서 Python으로 엔코더를 읽을 때 카운트가 누락됐습니다. 실시간 계수와 PID를 ESP32 펌웨어로 옮기고 Jetson에는 계산된 오도메트리만 전달했습니다.
2. **LiDAR와 ESP32 포트가 재부팅마다 뒤바뀌는 문제** — LiDAR는 CP2102, ESP32 DevKit V1은 CH340(`1a86:7523`)이지만 둘 다 `/dev/ttyUSB*`로 잡혀 번호가 부팅 순서에 따라 바뀔 수 있었습니다. 장치별 udev 규칙으로 `/dev/ydlidar`, `/dev/ttyUSB_ESP32`를 고정했습니다.
3. **SLAM에서 레이저 위치가 어긋나는 문제** — LiDAR 드라이버와 URDF가 같은 TF를 서로 다른 높이로 중복 발행하고 있었습니다. 드라이버 launch 대신 노드만 실행하고 URDF의 변환만 사용하도록 정리했습니다.
4. **카메라는 열리지만 마커가 보이지 않는 문제** — UVC 카메라가 영상·메타데이터 노드를 동시에 만들면서 잘못된 `/dev/video*`가 선택됐습니다. `ATTR{index}=="0"` 조건으로 영상 노드만 `/dev/video_cam`에 연결했습니다.

자세한 배선, 장치 설정 및 연동 계약은 [문서 목록](#디렉토리-구조)에서 확인할 수 있습니다.

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
├── potner_llm/           LLM 응답 파이프라인 (담당: LLM 파트)
└── potner_firmware/      ESP32 펌웨어 (PlatformIO, ROS 패키지 아님)

voice-chat-server/        음성 대화 웹서버 (담당: LLM 파트)
plant-robot-chat/         노트북용 웹 챗 테스트 앱 (Next.js) — voice-chat-server의
                          LLM_BACKEND=webchat 이 이 앱의 /api/chat 을 호출
tools/                    수동 실행 도구 (회전 보정, 시연 조종기, Nav2 액션 목)
tests/                    CI용 단위 테스트 (ROS 없이 순수 로직만)
docs/                     명세·환경 문서 (아래 표 참고), media/ 는 시연 사진·영상
legacy/                   ROS 2 전환 이전 코드 (참고용, 실행되지 않음)
```

| 문서 | 내용 |
|---|---|
| [`docs/CODE_STYLE.md`](docs/CODE_STYLE.md) | 팀 코드 스타일 규칙 |
| [`docs/DEVICE-JETSON.md`](docs/DEVICE-JETSON.md) | **젯슨이 할 일만 추린 서버 연동 명세 (서버 팀 관리, 가장 실전적) — 먼저 읽을 것** |
| [`docs/DEVICE-MQTT.md`](docs/DEVICE-MQTT.md) | 장치 공통 계약과 설계 근거 (서버 팀 관리) |
| [`docs/DEVICE-RASPBERRY.md`](docs/DEVICE-RASPBERRY.md) | 스테이션(라즈베리) 쪽 명세 — 참고용 |
| [`docs/MQTT_CONTRACT.md`](docs/MQTT_CONTRACT.md) | MQTT 로봇 쪽 구현 노트 |
| [`docs/NAVIGATE_TEST.md`](docs/NAVIGATE_TEST.md) | `command/navigate` 확인 절차 |
| [`docs/ARRIVAL_TEST.md`](docs/ARRIVAL_TEST.md) | 귀가 마중 확인 절차 |
| [`docs/JETSON_DISPLAY.md`](docs/JETSON_DISPLAY.md) | 젯슨 화면 세팅 (표정 표시 전 필독) |
| [`docs/WIRING.md`](docs/WIRING.md) | 장치별 배선·핀·역할 |
| [`docs/LLM_CAPABILITIES.md`](docs/LLM_CAPABILITIES.md) | 대화 LLM이 답할 수 있는 것/없는 것 |
| [`docs/LLM_QUESTION_EXAMPLES.md`](docs/LLM_QUESTION_EXAMPLES.md) | 시연·데모용 질문 카탈로그 |
| [`docs/LLM_SENSOR_INTEGRATION_PLAN.md`](docs/LLM_SENSOR_INTEGRATION_PLAN.md) | LLM 센서 실측값 연동 계획·전환 절차 |
| [`docs/SERVER_REQUEST_SENSOR_AUTH.md`](docs/SERVER_REQUEST_SENSOR_AUTH.md) | 서버 팀 요청 기록 — 장치용 센서 조회 인증 |

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
export POTNER_MQTT_PASSWORD='<브로커 비밀번호>'   # 서버 팀에게 받으세요. 저장소에 없습니다
```

비밀번호가 없으면 `mqtt_bridge` 가 접속을 포기하고 에러만 남깁니다. 계정
구조와 확인 방법은 [`docs/MQTT_CONTRACT.md`](docs/MQTT_CONTRACT.md) 를 보세요.

### 2. 워크스페이스 빌드

```bash
git clone https://github.com/jerry1700/Potner-Robot.git ~/potner_ws
cd ~/potner_ws
rosdep install --from-paths src --ignore-src -r -y
colcon build --symlink-install
source install/setup.bash
```

### 3. LiDAR 드라이버 설치

apt에 없어서 소스 빌드가 필요합니다. **반드시 `humble` 브랜치**를 받으세요.
기본 `master` 브랜치는 Foxy 시절 코드라 `declare_parameter` API가 바뀌어
Humble에서 빌드가 실패합니다.

```bash
# SDK
git clone https://github.com/YDLIDAR/YDLidar-SDK.git ~/YDLidar-SDK
mkdir -p ~/YDLidar-SDK/build && cd ~/YDLidar-SDK/build
cmake .. && make -j4 && sudo make install && sudo ldconfig

# ROS 2 드라이버 (humble 브랜치!)
cd ~/potner_ws/src
git clone -b humble https://github.com/YDLIDAR/ydlidar_ros2_driver.git
cd ~/potner_ws && colcon build --packages-select ydlidar_ros2_driver

# udev 등록 (/dev/ydlidar 심볼릭 링크 생성)
cd ~/potner_ws/src/ydlidar_ros2_driver/startup && sudo sh initenv.sh
```

`src/ydlidar_ros2_driver/` 는 `.gitignore` 에 있습니다. 외부 코드라 우리
저장소에 넣지 않고 각자 받습니다.

> **⚠️ `ydlidar_launch.py` 는 쓰지 마세요.**
> 그 launch는 `base_link → laser_frame` 을 2cm로 발행하는데, 우리 URDF는
> 폴 높이를 반영해 48.8cm로 발행합니다. 둘 다 켜면 같은 변환이 두 개
> 생겨 SLAM이 스캔 위치를 잡지 못합니다. `robot.launch.py` 가 드라이버
> 노드만 띄우고 파라미터는 `config/ydlidar.yaml` 로 넘깁니다.

동작 확인 (X4 Pro 기준 약 11Hz):

```bash
ros2 launch potner_bringup robot.launch.py
ros2 topic hz /scan     # 다른 터미널
```

### 4. USB 장치 이름 고정 (중요)

LiDAR와 ESP32가 **둘 다 `/dev/ttyUSB*`** 로 잡히지만 칩셋은 다릅니다. LiDAR는 CP2102이고, 현재 사용하는 ESP32 DevKit V1은 CH340(`1a86:7523`)입니다. 꽂는 순서나 부팅 타이밍에 따라 `/dev/ttyUSB*` 번호가 바뀔 수 있어, 장치 고정 규칙이 필요합니다.

LiDAR는 3단계의 `initenv.sh` 가 `/dev/ydlidar` 를 만들어줍니다. **ESP32는
직접 등록**하며, CH340의 vendor/product ID를 기준으로 `/dev/ttyUSB_ESP32` 심볼릭 링크를 만듭니다.

```bash
# ESP32만 꽂은 상태에서 vendor/product ID 확인
udevadm info -a -n /dev/ttyUSB0 | grep -E 'idVendor|idProduct' | head -4
```

`/etc/udev/rules.d/99-potner.rules` 를 만들고:

```
SUBSYSTEM=="tty", ATTRS{idVendor}=="1a86", ATTRS{idProduct}=="7523", SYMLINK+="ttyUSB_ESP32"
```

```bash
sudo udevadm control --reload-rules && sudo udevadm trigger
```

`config/potner_params.yaml` 의 `base_driver.serial_port` 가 이 이름을
씁니다.

**카메라도 같은 문제가 있습니다.** UVC 웹캠은 영상용과 메타데이터용
`/dev/video*` 노드를 함께 만들기 때문에 번호가 둘 이상 생기고, 어느 쪽이
영상인지는 번호로 알 수 없습니다. 게다가 그 번호가 재부팅마다 바뀝니다.
어긋나면 `v4l2_camera` 가 조용히 물러나고 `marker_detector` 는 영영 아무것도
발행하지 않아서, 도킹이 15초 뒤 "마커를 찾지 못함" 으로 끝납니다 — 원인이
카메라라는 단서가 어디에도 남지 않습니다.

카메라만 꽂은 상태에서 아래 한 줄이면 규칙이 만들어집니다. 벤더·제품 ID를
장치에서 직접 읽으므로 손으로 채워 넣을 값이 없습니다.

```bash
V=$(udevadm info -a -n /dev/video0 | grep -m1 'ATTRS{idVendor}' | tr -d ' "' | cut -d= -f3); P=$(udevadm info -a -n /dev/video0 | grep -m1 'ATTRS{idProduct}' | tr -d ' "' | cut -d= -f3); echo "SUBSYSTEM==\"video4linux\", ATTRS{idVendor}==\"$V\", ATTRS{idProduct}==\"$P\", ATTR{index}==\"0\", SYMLINK+=\"video_cam\"" | sudo tee /etc/udev/rules.d/99-potner-camera.rules
```

`ATTR{index}=="0"` 이 핵심입니다. 이게 없으면 같은 웹캠의 메타데이터 노드에도
링크가 걸려서 둘 중 아무 쪽이나 잡힙니다.

```bash
sudo udevadm control --reload-rules && sudo udevadm trigger
ls -l /dev/video_cam    # 링크가 생겼는지
python3 -c "import cv2; cap=cv2.VideoCapture('/dev/video_cam'); ok,f=cap.read(); print(ok, f.shape if ok else ''); cap.release()"
```

`robot.launch.py` 의 `camera_device` 인자가 이 이름을 기본값으로 씁니다.
규칙을 만들기 전이라면 `camera_device:=/dev/video0` 으로 넘기세요.

### 5. 단계별로 띄우기

한 번에 다 켜면 무엇이 고장났는지 알 수 없습니다. 순서대로 확인하세요.

```bash
# ① 형상 확인 — 로봇 없이 노트북에서도 됩니다
ros2 launch potner_description view_model.launch.py

# ② 로봇 본체 (센서 + 모터 + 안전 + 표정 화면)
#    SSH 에서 띄우면 표정 창이 안 뜹니다. export DISPLAY=:0 을 먼저 하세요
#    → docs/JETSON_DISPLAY.md
ros2 launch potner_bringup robot.launch.py

# ③ 지도 만들기 — 키보드로 천천히 몰면서 집을 한 바퀴
ros2 launch potner_bringup slam.launch.py
ros2 run teleop_twist_keyboard teleop_twist_keyboard \
    --ros-args -r cmd_vel:=cmd_vel_teleop
ros2 run nav2_map_server map_saver_cli -f ~/potner_ws/maps/home

# ④ 자율주행
ros2 launch potner_bringup nav2.launch.py map:=$HOME/potner_ws/maps/home.yaml
```

### 6. ESP32 펌웨어

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

## 테스트

```bash
pytest tests/ -v
```

ROS 2 없이 돌아갑니다. 젠킨스 CI가 이 테스트와 flake8 문법 검사를
자동으로 실행합니다 ([Jenkinsfile](Jenkinsfile)).
