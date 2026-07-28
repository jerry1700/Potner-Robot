import cv2
import cv2.aruco as aruco
import numpy as np
import time

# 원인 파악을 위해 외부 컨트롤러를 쓰지 않고 직접 모터 라이브러리를 불러옵니다.
import board
import busio
from adafruit_pca9685 import PCA9685


class PWMThrottleHat:
    def __init__(self, pwm, channel):
        self.pwm = pwm
        self.channel = channel
        self.pwm.frequency = 60

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


def main():
    print("Initiating straight docking test (Direct motor control version).")

    # 모터 초기화
    print("I2C 및 모터 초기화 중...")
    i2c = busio.I2C(board.SCL, board.SDA)
    pca = PCA9685(i2c)
    pca.frequency = 60
    motor_hat = PWMThrottleHat(pca, channel=0)
    print("모터 초기화 성공!")

    # 카메라 초기화
    cap = cv2.VideoCapture(0)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

    aruco_dict = aruco.getPredefinedDictionary(aruco.DICT_6X6_250)
    parameters = aruco.DetectorParameters()
    detector = aruco.ArucoDetector(aruco_dict, parameters)

    focal_length_px = 600.0
    real_marker_width_cm = 4.0
    target_distance_cm = 20.0

    last_speed = None

    try:
        while True:
            ret, frame = cap.read()
            if not ret:
                print("Error: Unable to read camera frame.")
                break

            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            corners, ids, _ = detector.detectMarkers(gray)

            if ids is not None:
                target_corners = corners[0][0]
                pixel_width = np.linalg.norm(target_corners[0] - target_corners[1])

                if pixel_width > 0:
                    current_distance = (
                        real_marker_width_cm * focal_length_px
                    ) / pixel_width
                    print(
                        f"Marker detected. Current distance: {current_distance:.1f}cm"
                    )

                    if current_distance <= target_distance_cm:
                        print(
                            f"Target distance ({target_distance_cm}cm) reached. Stopping motors."
                        )
                        motor_hat.set_throttle(0.0)
                        break
                    else:
                        print("Moving forward (Throttle 50%)")
                        if last_speed != 0.5:
                            motor_hat.set_throttle(0.5)
                            last_speed = 0.5
            else:
                print("Searching for marker... (Motors stopped)")
                if last_speed != 0.0:
                    motor_hat.set_throttle(0.0)
                    last_speed = 0.0

            time.sleep(0.1)

    except KeyboardInterrupt:
        print("\nTest interrupted by user.")
    finally:
        motor_hat.set_throttle(0.0)
        pca.deinit()
        cap.release()
        print("Test terminated.")


if __name__ == "__main__":
    main()
