pipeline {
    agent any

    environment {
        // Python 환경 설정 (EC2 환경에 맞게 python3 사용 권장)
        PYTHON_CMD = 'python3'
        
        // CI 환경임을 알리는 플래그. 코드 내에서 이 환경변수를 감지하여 하드웨어(GPIO, Camera 등) 로직을 Mock 처리해야 함
        IS_CI_ENV = 'true'
    }

    stages {
        stage('Checkout') {
            steps {
                echo 'Checking out source code...'
                // GitLab Webhook에 의해 트리거된 Branch(Raspberry-master 또는 Robot-master)가 Checkout 됩니다.
                checkout scm
            }
        }

        stage('Python 환경 구성') {
            steps {
                echo 'Setting up Python Virtual Environment...'
                sh '''
                    ${PYTHON_CMD} -m venv venv
                '''
            }
        }

        stage('Dependency Install') {
            steps {
                echo 'Installing dependencies...'
                sh '''
                    . venv/bin/activate
                    pip install --upgrade pip

                    # requirements_ci.txt 는 저장소에 커밋된 파일입니다. 예전에는
                    # requirements.txt(젯슨 전용)에서 grep 으로 즉석 생성했는데,
                    # 그러면 테스트가 쓰지 않는 패키지 수십 개(matplotlib, pandas,
                    # ultralytics, CPU torch 등 기가바이트급)까지 매번 설치해서
                    # 느리고, 그중 하나만 pip 에서 깨져도 CI 전체가 죽습니다.
                    # 테스트가 실제로 import 하는 것만 골라 둔 목록을 씁니다.
                    pip install -r requirements_ci.txt

                    # CI 검증을 위한 추가 패키지 설치
                    pip install pytest flake8
                '''
            }
        }

        stage('Syntax/Lint') {
            steps {
                echo 'Running Syntax and Lint checks...'
                sh '''
                    . venv/bin/activate
                    # 심각한 문법 오류(Syntax error) 및 정의되지 않은 변수 참조 등만 찾도록 설정
                    # venv, colcon 빌드 산출물(build/install/log), PlatformIO 캐시는 제외합니다.
                    flake8 . --count --select=E9,F63,F7,F82 --show-source --statistics \
                        --exclude=venv,.git,__pycache__,build,install,log,.pio
                '''
            }
        }

        stage('Unit Test') {
            steps {
                echo 'Running Unit Tests...'
                sh '''
                    . venv/bin/activate
                    # CI 서버에는 ROS 2가 없습니다. 그래서 rclpy를 import 하는 노드 파일은
                    # 테스트하지 않고, 계산 로직만 담긴 순수 파이썬 모듈을 검증합니다.
                    #   - 차동 구동 기구학 / 오도메트리 적분
                    #   - ESP32 시리얼 프로토콜 (체크섬)
                    #   - 임무 우선순위 판단
                    #   - 도킹 P 제어
                    # 노드 파일과 계산 로직을 파일 단위로 분리해 둔 이유가 이것입니다.
                    # 경로 설정은 tests/conftest.py 가 담당합니다.
                    pytest tests/ -v
                '''
            }
        }
    }

    post {
        success {
            echo 'SUCCESS: CI Pipeline completed successfully!'
        }
        failure {
            echo 'FAILURE: CI Pipeline failed. Please check the logs.'
        }
    }
}
