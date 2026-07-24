# Potner: 반려식물 AIoT 로봇 시스템 (Companion Plant Robot System)

## 프로젝트 개요 (Overview)
Potner는 반려식물의 상태를 모니터링하고 스스로 돌보는 자율주행 AIoT 로봇 소프트웨어 시스템입니다. 본 시스템은 토양 수분, 주변 온도, 조도 및 배터리 잔량과 같은 환경 요인을 실시간으로 측정합니다. 수집된 데이터를 바탕으로 시스템은 우선순위에 따른 작업을 자율적으로 스케줄링하며, 컴퓨터 비전(Computer Vision) 기반의 서보잉(Servoing) 기술을 활용해 충전, 급수, 일광욕, 송풍 등 특수 목적 스테이션으로 정확하게 도킹합니다.

## 핵심 기능 (Key Features)
- **지능형 작업 스케줄링 (Intelligent Task Scheduling)**: 실시간 센서 데이터를 분석하여 배터리, 수분, 일조량, 온도 등 가장 시급한 작업을 우선순위에 따라 스케줄링합니다.
- **비전 기반 자율 도킹 (Vision-Based Autonomous Docking)**: OpenCV 및 ArUco 마커를 활용하여 목표 스테이션을 정밀하게 인식하고 조향 모터를 제어하는 비전 서보잉 알고리즘을 수행합니다.
- **동적 장애물 회피 (Dynamic Obstacle Avoidance)**: Ultralytics YOLOv11 모델을 통한 객체 탐지와 초음파 센서를 결합하여, 주행 중 발생하는 돌발 장애물을 실시간으로 인지하고 우회합니다.
- **모듈형 하드웨어 추상화 (Modular Hardware Abstraction)**: I2C 통신 기반의 PCA9685, DC/서보 모터 및 GPIO 센서와 쉽게 통합될 수 있도록 확장성 있는 하드웨어 제어 모듈을 제공합니다.

## 디렉토리 구조 (Directory Structure)

```text
potner/
├── main.py                          # 시스템 메인 컨트롤러 및 상태 머신(State Machine) 구동
├── vision/
│   ├── auto_docking_vision.py       # YOLOv11 및 ArUco 기반 비전 서보잉 및 장애물 회피 로직
│   └── create_station_markers.py    # 인식률을 높인 ArUco 마커 생성 스크립트
├── navigation/
│   └── wheel_motor_controller.py    # 하드웨어 모터 드라이버 제어 (DC/서보 모터 PWM 제어)
├── sensors/
│   ├── moisture.py                  # 토양 수분 센서 모듈 (Mock)
│   └── ultrasonic.py                # 초음파 거리 센서 모듈 (Mock)
└── test_straight_docking.py         # 하드웨어 모터 및 비전 동기화 테스트 스크립트
```

## 시작하기 (Getting Started)

### 요구 사항 (Prerequisites)
- Python 3.9+ 이상
- OpenCV, NumPy, Ultralytics YOLO 모듈
- Adafruit CircuitPython 라이브러리 (실제 타겟 하드웨어 구동 시 필요)

### 설치 및 실행 방법 (Installation & Execution)
1. Python 가상환경을 생성하고 활성화합니다:
   ```bash
   python -m venv venv
   # Windows 환경
   .\venv\Scripts\activate
   # Linux / macOS 환경
   source venv/bin/activate
   ```
2. 프로젝트에 필요한 의존성 패키지를 설치합니다:
   ```bash
   pip install -r requirements.txt
   ```
3. 메인 컨트롤러 시나리오 시뮬레이션을 실행합니다:
   ```bash
   python main.py
   ```

## 향후 개발 로드맵 (Development Roadmap)
- **하드웨어 제어 통합**: `navigation/wheel_motor_controller.py` 내 시리얼/PWM 모터 제어 코드의 실기기 캘리브레이션 및 연동 완료.
- **센서 로직 배포**: `sensors/` 디렉토리 내의 Mock(모의) 반환값을 실제 라즈베리파이/아두이노 GPIO 센서 판독 로직으로 대체.
- **사용자 인터랙션 기능**: 로봇 대기(IDLE) 상태 중 YOLOv11을 통해 사람이 감지될 경우 "맞이(Greeting)" 모드로 전환되는 상호작용 행동 패턴 추가.
