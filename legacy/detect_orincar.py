from adafruit_motor import motor
from adafruit_pca9685 import PCA9685
from adafruit_servokit import ServoKit
import board
import busio
import time
import cv2
import torch
from ultralytics import YOLO

model = YOLO("custom.pt")
device = "cuda" if torch.cuda.is_available() else "cpu"
model.to(device)
cap = cv2.VideoCapture(0)
if not cap.isOpened():
    print("Error: Could not open camera.")


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


i2c = busio.I2C(board.SCL, board.SDA)
pca = PCA9685(i2c)
pca.frequency = 60
motor_hat = PWMThrottleHat(pca, channel=0)
kit = ServoKit(channels=16, i2c=i2c, address=0x60)
pan = 100
kit.servo[0].angle = pan


def i2c_scan(i2c):
    while not i2c.try_lock():
        pass
    try:
        devices = i2c.scan()
        return devices
    finally:
        i2c.unlock()


motor_hat.set_throttle(0)
kit.servo[0].angle = pan
speed = 0
use_cuda = cv2.cuda.getCudaEnabledDeviceCount() > 0
if use_cuda:
    print("CUDA가 활성화되었습니다.")
else:
    print("CUDA를 사용할 수 없습니다. CPU 모드로 전환합니다.")
try:
    while True:
        ret, frame = cap.read()
        if not ret:
            print("Error: Could not read frame.")
            break
        # GPU를 사용한 이미지 처리
        if use_cuda:
            gpu_frame = cv2.cuda.GpuMat()
            gpu_frame.upload(frame)
            # GPU에서 컬러 변환 (BGR to HSV) -> 다시 BGR로 변환하여 출력
            gpu_hsv = cv2.cuda.cvtColor(gpu_frame, cv2.COLOR_BGR2HSV)
            gpu_processed = cv2.cuda.cvtColor(gpu_hsv, cv2.COLOR_HSV2BGR)
            frame = gpu_processed.download()
        else:
            frame = frame
        # YOLO 모델을 사용하여 객체(사람) 감지
        results = model(frame)
        for result in results:
            for box in result.boxes:
                x1, y1, x2, y2 = box.xyxy[0].tolist()
                confidence = box.conf[0].item()
                class_id = box.cls[0].item()
                label = f"{model.names[int(class_id)]}: {confidence:.2f}"
                print(label)
                if model.names[int(class_id)] == "person":
                    cv2.rectangle(
                        frame, (int(x1), int(y1)), (int(x2), int(y2)), (0, 255, 0), 2
                    )
                    cv2.putText(
                        frame,
                        label,
                        (int(x1), int(y1) - 10),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.5,
                        (0, 255, 0),
                        2,
                    )
                    person_center_x = (x1 + x2) / 2
                    frame_center_x = 320 / 2
                    speed = 0.7
                    if person_center_x < frame_center_x - 30:
                        print("Turn left")
                        kit.servo[0].angle = max(80, kit.servo[0].angle - 5)
                    elif person_center_x > frame_center_x + 30:
                        print("Turn right")
                        kit.servo[0].angle = min(120, kit.servo[0].angle + 5)
                else:
                    speed = 0
        motor_hat.set_throttle(speed)
        cv2.imshow("YOLOv8 Detection", frame)
        if cv2.waitKey(1) & 0xFF == ord("q"):
            break
    cap.release()
    cv2.destroyAllWindows()
except KeyboardInterrupt:
    pass
finally:
    motor_hat.set_throttle(0)
    kit.servo[0].angle = 100
    pca.deinit()
    print("Program stopped and motor stopped.")
