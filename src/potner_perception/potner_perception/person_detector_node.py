"""person_detector — 카메라에서 사람을 찾습니다.

별도 노드(= 별도 프로세스)로 분리한 것이 핵심입니다. legacy 코드에서는
YOLO 추론이 도킹 루프 안에 섞여 있어서, 추론이 도는 동안 주행 판단이
통째로 멈췄습니다. 파이썬 GIL 때문에 스레드로 나눠도 해결되지 않습니다.
프로세스를 나눠야 비로소 젯슨의 코어를 따로 쓸 수 있습니다.
"""

import rclpy
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import Image
from std_msgs.msg import Bool

try:
    from cv_bridge import CvBridge
    from ultralytics import YOLO
except ImportError:  # 개발 PC에서 파일만 열어볼 때
    CvBridge = None
    YOLO = None

# COCO 클래스 번호. 0=사람, 15=고양이, 16=개
PERSON_CLASS = 0
PET_CLASSES = (15, 16)


class PersonDetector(Node):
    def __init__(self):
        super().__init__("person_detector")

        self.declare_parameter("model_path", "yolo11n.pt")
        self.declare_parameter("confidence", 0.5)
        self.declare_parameter("inference_period", 0.2)  # 5Hz면 환영 반응에 충분
        self.declare_parameter("device", "cuda")

        self._confidence = self.get_parameter("confidence").value
        self._device = self.get_parameter("device").value
        self._latest_frame = None
        self._bridge = CvBridge() if CvBridge else None
        self._model = self._load_model()

        self.create_subscription(
            Image, "image_raw", self._on_image, qos_profile_sensor_data
        )
        self._person_pub = self.create_publisher(Bool, "perception/person_present", 10)
        self._pet_pub = self.create_publisher(Bool, "perception/pet_present", 10)

        period = self.get_parameter("inference_period").value
        self.create_timer(period, self._infer)

    def _load_model(self):
        if YOLO is None:
            self.get_logger().warn("ultralytics 미설치 — 추론 없이 대기합니다.")
            return None

        path = self.get_parameter("model_path").value
        self.get_logger().info(f"YOLO 모델 로딩: {path}")
        model = YOLO(path)

        # TODO: 젯슨에서는 TensorRT 엔진으로 변환하면 추론이 크게 빨라집니다.
        #   yolo export model=yolo11n.pt format=engine device=0 half=True
        #   변환 후 model_path 를 yolo11n.engine 으로 바꾸세요.
        return model

    def _on_image(self, msg: Image):
        """프레임은 저장만 하고 추론은 타이머에서 합니다.

        콜백에서 바로 추론하면 카메라가 30fps로 밀어넣는 프레임마다
        YOLO가 돌면서 큐가 밀립니다. 최신 프레임 하나만 들고 있다가
        정해진 주기로 처리하는 게 맞습니다.
        """
        self._latest_frame = msg

    def _infer(self):
        if self._model is None or self._latest_frame is None:
            return

        frame = self._bridge.imgmsg_to_cv2(self._latest_frame, "bgr8")
        self._latest_frame = None

        results = self._model.predict(frame, verbose=False, device=self._device)

        person = False
        pet = False
        for result in results:
            for box in result.boxes:
                if float(box.conf[0]) < self._confidence:
                    continue
                class_id = int(box.cls[0])
                if class_id == PERSON_CLASS:
                    person = True
                elif class_id in PET_CLASSES:
                    pet = True

        self._person_pub.publish(Bool(data=person))
        self._pet_pub.publish(Bool(data=pet))


def main(args=None):
    rclpy.init(args=args)
    node = PersonDetector()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
