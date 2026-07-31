import logging
import time
import os

if os.environ.get('IS_CI_ENV') == 'true':
    HARDWARE_AVAILABLE = False
    print("CI Environment Detected. Running motor controller in simulation mode.")
else:
    try:
        import board
        import busio
        from adafruit_pca9685 import PCA9685
        from adafruit_servokit import ServoKit

        HARDWARE_AVAILABLE = True
    except Exception:
        HARDWARE_AVAILABLE = False
        print("WARNING: adafruit modules not found or failed. Running in simulation mode.")

logger = logging.getLogger(__name__)


class PWMThrottleHat:
    def __init__(self, pwm, channel):
        self.pwm = pwm
        self.channel = channel
        # 저가형 모터 기판의 안정적인 신호 인식을 위해 주파수를 100Hz로 설정
        self.pwm.frequency = 100

    def set_throttle(self, throttle):
        pulse = int(0xFFFF * abs(throttle))
        if throttle < 0:
            self.pwm.channels[self.channel + 5].duty_cycle = pulse
            self.pwm.channels[self.channel + 4].duty_cycle = 0
            self.pwm.channels[self.channel + 3].duty_cycle = 0xFFFF
        elif throttle > 0:
            self.pwm.channels[self.channel + 5].duty_cycle = pulse
            self.pwm.channels[self.channel + 4].duty_cycle = 0xFFFF
            self.pwm.channels[self.channel + 3].duty_cycle = 0
        else:
            self.pwm.channels[self.channel + 5].duty_cycle = 0
            self.pwm.channels[self.channel + 4].duty_cycle = 0
            self.pwm.channels[self.channel + 3].duty_cycle = 0


class MotorController:
    """
    하드웨어 모터 드라이버(DC 모터 구동 및 서보 모터 조향) 제어를 위한 클래스.
    """

    def __init__(self) -> None:
        self.is_connected: bool = False
        self.i2c = None
        self.pca = None
        self.motor_hat = None
        self.kit = None
        self.pan = 100

        # 중복 명령 전송 방지용 캐시
        self.last_linear = None
        self.last_angular = None

        # 긴급 정지 플래그
        self.emergency_stop_active = False

        if HARDWARE_AVAILABLE:
            try:
                self.i2c = busio.I2C(board.SCL, board.SDA)

                # 1. 서보 모터(0x60) 초기화 (이 칩은 자체적으로 50Hz로 셋팅됨)
                self.kit = ServoKit(channels=16, i2c=self.i2c, address=0x60)

                # 2. DC 모터(0x40) 초기화
                self.pca = PCA9685(self.i2c, address=0x40)

                # 3. DC 모터 칩 주파수를 100Hz로 설정
                self.pca.frequency = 100
                self.motor_hat = PWMThrottleHat(self.pca, channel=0)
                self.kit.servo[0].angle = self.pan

                self.is_connected = True
                logger.info(
                    "Motor controller hardware connected successfully. (PWM frequency patched to 100Hz)"
                )
            except Exception as e:
                logger.error(f"Failed to initialize motors: {e}")
                self.is_connected = False
        else:
            logger.info("Motor controller initialized in Simulation/Debug mode.")

    def send_command(
        self, linear_speed: float, angular_speed: float, force: bool = False
    ) -> None:
        """
        주어진 직진(linear) 및 회전(angular) 속도 명령을 모터 제어 신호로 변환하여 전송합니다.

        Args:
            linear_speed (float): 전후진 속도 (-1.0 ~ 1.0)
            angular_speed (float): 조향을 위한 회전 속도
            force (bool): True일 경우 긴급 정지 상태를 무시하고 명령을 강제 전송 (회피 기동용)
        """
        if self.emergency_stop_active and not force:
            # 긴급 정지 상태에서는 어떠한 주행 명령도 무시합니다. (회피 기동 등 특별한 해제 절차 전까지)
            return

        if self.is_connected:
            # 1. DC 모터 직진/후진 제어 (상태 캐싱으로 I2C 통신 최소화)
            if self.last_linear != linear_speed:
                try:
                    self.motor_hat.set_throttle(linear_speed)
                    self.last_linear = linear_speed
                except OSError as e:
                    logger.error(f"I2C 통신 노이즈 발생 (DC 모터 무시됨): {e}")

            # 2. 서보 모터 좌/우 회전 제어
            if self.last_angular != angular_speed:
                steering_offset = angular_speed * 40.0
                target_pan = 100 + steering_offset

                # 물리적 한계점(0~180도) 방어
                target_pan = max(0, min(180, target_pan))
                try:
                    self.kit.servo[0].angle = int(target_pan)
                    self.pan = target_pan
                    self.last_angular = angular_speed
                except OSError as e:
                    logger.error(f"I2C 통신 노이즈 발생 (서보 모터 무시됨): {e}")

    def emergency_stop(self) -> None:
        """즉각적으로 모터를 정지하고, 긴급 정지 상태로 진입합니다."""
        logger.error("!!! EMERGENCY STOP ACTIVATED !!!")
        self.emergency_stop_active = True
        self.stop()

    def clear_emergency_stop(self) -> None:
        """긴급 정지 상태를 해제하여 다시 모터 제어가 가능하도록 합니다."""
        logger.info("Emergency stop cleared. Motors ready.")
        self.emergency_stop_active = False

    def stop(self) -> None:
        if self.is_connected:
            try:
                if self.last_linear != 0:
                    self.motor_hat.set_throttle(0)
                    self.last_linear = 0
                if self.pan != 100:
                    self.kit.servo[0].angle = 100
                    self.pan = 100
                    self.last_angular = 0
            except OSError as e:
                logger.error(f"I2C 통신 노이즈 발생 (모터 정지 무시됨): {e}")

    def cleanup(self) -> None:
        if self.is_connected:
            self.stop()
            if self.pca:
                self.pca.deinit()
