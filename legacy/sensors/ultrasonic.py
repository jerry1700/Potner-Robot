import logging
import time

logger = logging.getLogger(__name__)

# 라즈베리파이/젯슨 GPIO 설정
import os

if os.environ.get('IS_CI_ENV') == 'true':
    HARDWARE_AVAILABLE = False
    logger.info("CI Environment Detected. Running ultrasonic sensor in simulation mode.")
else:
    try:
        import RPi.GPIO as GPIO

        HARDWARE_AVAILABLE = True

        # 초음파 센서 핀 번호 설정 (BCM 기준)
        TRIG = 23
        ECHO = 24

        try:
            GPIO.setmode(GPIO.BCM)
        except ValueError:
            # Adafruit Blinka 등 다른 모듈이 이미 GPIO 모드를 설정한 경우 충돌 무시
            logger.warning(
                "GPIO mode is already set by another library (e.g. adafruit-blinka). Using existing mode."
            )

        GPIO.setwarnings(False)

        try:
            GPIO.setup(TRIG, GPIO.OUT)
            GPIO.setup(ECHO, GPIO.IN)
            logger.info("Ultrasonic sensor hardware initialized.")
        except ValueError as e:
            logger.error(
                f"Pin setup failed (possibly due to mode conflict): {e}. Falling back to simulation."
            )
            HARDWARE_AVAILABLE = False

    except Exception:
        HARDWARE_AVAILABLE = False
        logger.warning(
            "RPi.GPIO module not found or failed. Running ultrasonic sensor in simulation mode."
        )


def get_distance() -> float:
    """
    초음파 센서(HC-SR04)로부터 전방 장애물까지의 거리(cm)를 실제 하드웨어 핀에서 측정합니다.

    Returns:
        float: 측정된 물리적 거리 (cm)
    """
    if not HARDWARE_AVAILABLE:
        # 시뮬레이션 환경: 가상의 안전 거리(999cm)를 반환합니다.
        return 999.0

    try:
        # 10us 펄스를 발생시켜 초음파 송신
        GPIO.output(TRIG, True)
        time.sleep(0.00001)
        GPIO.output(TRIG, False)

        pulse_start = time.time()
        pulse_end = time.time()

        # 무한 루프 방지를 위한 타임아웃 설정 (약 0.04초 = 최대 측정 거리 약 6.8m)
        timeout = pulse_start + 0.04

        # ECHO 핀이 High가 될 때까지 대기 (시작 시간 기록)
        while GPIO.input(ECHO) == 0 and time.time() < timeout:
            pulse_start = time.time()

        # ECHO 핀이 Low가 될 때까지 대기 (종료 시간 기록)
        while GPIO.input(ECHO) == 1 and time.time() < timeout:
            pulse_end = time.time()

        pulse_duration = pulse_end - pulse_start

        # 음속(343m/s = 34300cm/s)을 이용한 왕복 거리 계산 (34300 / 2 = 17150)
        distance = pulse_duration * 17150

        # HC-SR04 유효 측정 범위(2cm ~ 400cm)를 벗어나면 안전 값 반환
        if distance < 2.0 or distance > 400.0:
            return 999.0

        return round(distance, 1)

    except Exception as e:
        logger.error(f"Ultrasonic sensor read error: {e}")
        return 999.0
