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
                    
                    # CI 서버(Linux)와 실제 로봇(Jetson) 환경의 차이로 인한 CUDA 패키지 충돌 방지
                    # 원본에서 cuda, nvidia, torch 관련 내용을 제외한 CI용 요구사항 파일 생성
                    grep -vE 'cuda-toolkit|nvidia-|torch' requirements.txt > requirements_ci.txt
                    
                    # CI(서버) 환경에서는 무거운 GPU 버전 대신 가벼운 CPU 버전의 PyTorch를 설치
                    pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cpu
                    
                    # 나머지 프로젝트 의존성 설치
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
                    flake8 . --count --select=E9,F63,F7,F82 --show-source --statistics
                '''
            }
        }

        stage('Unit Test') {
            steps {
                echo 'Running Unit Tests...'
                sh '''
                    . venv/bin/activate
                    # 테스트 폴더 실행
                    # 주의: 하드웨어 접근 로직은 CI 환경에서 에러를 뿜으므로, 
                    # IS_CI_ENV 환경변수를 통해 pytest 내부에서 Mock 처리되도록 구성되어야 합니다.
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
