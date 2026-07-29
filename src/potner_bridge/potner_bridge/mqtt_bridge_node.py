"""mqtt_bridge — Spring Boot 서버 및 스테이션과 이어지는 유일한 창구.

ROS 2와 MQTT는 서로 다른 세계입니다. 로봇 내부는 ROS 토픽으로,
집 밖과는 MQTT로 이야기합니다. 이 노드가 그 사이를 번역합니다.

    ROS -> MQTT   식물 센서값, 로봇 상태, 배터리
    MQTT -> ROS   서버가 내려보낸 대사, 스테이션 도착 신호, 원격 명령

주의: 이 노드가 죽거나 인터넷이 끊겨도 주행과 안전 정지는 계속 돌아야
합니다. 그래서 Nav2나 safety 노드는 여기에 전혀 의존하지 않습니다.
"""

import json

import rclpy
from rclpy.node import Node
from std_msgs.msg import Bool, Float32, String

try:
    import paho.mqtt.client as mqtt
except ImportError:
    mqtt = None

# 스테이션(Raspberry-master)이 대기 온습도·조도를 올려보내는 토픽.
# 양쪽 팀이 같은 이름을 써야 합니다.
STATION_TELEMETRY_TOPIC = "potner/station/telemetry"


class MqttBridge(Node):
    def __init__(self):
        super().__init__("mqtt_bridge")

        self.declare_parameter("broker_host", "localhost")
        self.declare_parameter("broker_port", 1883)
        self.declare_parameter("device_id", "potner-01")
        self.declare_parameter("publish_period", 10.0)  # 센서 업로드 주기(초)

        self._device_id = self.get_parameter("device_id").value
        self._latest = {}

        # ROS -> MQTT 로 올려보낼 값들
        for topic, key in (
            ("plant/moisture", "moisture"),
            ("plant/lux", "lux"),
            ("plant/temperature", "temperature"),
            ("battery/percent", "battery"),
        ):
            self.create_subscription(
                Float32, topic, self._capture(key), 10
            )
        self.create_subscription(String, "mission/state", self._capture_str("state"), 10)

        # 스테이션 서비스 요청은 모아 올리지 않고 즉시 중계합니다. 도킹이
        # 끝난 직후 급수나 송풍을 시작해야 하는데, 텔레메트리 주기(10초)를
        # 기다리면 로봇이 스테이션 앞에서 멍하니 서 있게 됩니다.
        self.create_subscription(String, "station/request", self._on_request, 10)

        # MQTT -> ROS 로 내려올 값들
        self._speech_pub = self.create_publisher(String, "tts/say", 10)
        self._station_pub = self.create_publisher(Bool, "station/docked", 10)

        # 대기 온도는 스테이션이 잽니다. 로봇에도 DHT11 이 있지만 리눅스에서
        # 원선 프로토콜을 읽는 건 마이크로초 타이밍이 필요해 자주 실패합니다.
        # 발표자료 아키텍처대로 스테이션(ESP32)이 재서 올려보내는 값을 씁니다.
        self._temperature_pub = self.create_publisher(Float32, "plant/temperature", 10)

        self._client = self._connect()
        self.create_timer(self.get_parameter("publish_period").value, self._upload)

    # --- MQTT ---

    def _connect(self):
        if mqtt is None:
            self.get_logger().warn("paho-mqtt 미설치 — 브리지가 동작하지 않습니다.")
            return None

        host = self.get_parameter("broker_host").value
        port = self.get_parameter("broker_port").value
        client = mqtt.Client(client_id=self._device_id)
        client.on_connect = self._on_connect
        client.on_message = self._on_message

        try:
            client.connect(host, port, keepalive=60)
            client.loop_start()
            self.get_logger().info(f"MQTT 연결: {host}:{port}")
        except Exception as exc:
            # 인터넷이 없어도 로봇 자체는 돌아야 하므로 죽지 않습니다.
            self.get_logger().error(f"MQTT 연결 실패: {exc}")
            return None
        return client

    def _on_connect(self, client, userdata, flags, rc):
        client.subscribe(f"potner/{self._device_id}/speech")
        client.subscribe("potner/station/+/docked")
        client.subscribe(STATION_TELEMETRY_TOPIC)

    def _on_message(self, client, userdata, message):
        topic = message.topic
        payload = message.payload.decode("utf-8", errors="ignore")

        if topic.endswith("/speech"):
            # 서버 LLM이 만든 대사. 음성 합성은 speaker 노드가 합니다.
            self._speech_pub.publish(String(data=payload))
        elif topic.endswith("/docked"):
            self._station_pub.publish(Bool(data=payload.strip() in ("1", "true", "True")))
        elif topic == STATION_TELEMETRY_TOPIC:
            self._on_station_telemetry(payload)

    def _on_station_telemetry(self, payload: str):
        """스테이션이 올려보낸 환경값에서 대기 온도를 꺼냅니다.

        ★ 스테이션(Raspberry-master) 담당자와 토픽 이름과 JSON 키를 맞춰야
          합니다. 한쪽만 바꾸면 조용히 값이 안 들어옵니다.
        """
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            self.get_logger().warn(
                f"스테이션 환경값이 JSON 이 아닙니다: {payload[:40]}",
                throttle_duration_sec=30.0,
            )
            return

        temperature = data.get("temperature")
        if temperature is None:
            return

        try:
            self._temperature_pub.publish(Float32(data=float(temperature)))
        except (TypeError, ValueError):
            self.get_logger().warn(
                f"온도값을 숫자로 읽을 수 없습니다: {temperature!r}",
                throttle_duration_sec=30.0,
            )

    # --- ROS ---

    def _capture(self, key):
        def callback(msg):
            self._latest[key] = float(msg.data)

        return callback

    def _capture_str(self, key):
        def callback(msg):
            self._latest[key] = msg.data

        return callback

    def _on_request(self, msg: String):
        """스테이션에 서비스 시작을 요청합니다 (급수, 송풍 등)."""
        if self._client is None:
            self.get_logger().warn(
                f"MQTT 미연결 — 스테이션 요청을 보내지 못했습니다: {msg.data}"
            )
            return

        topic = f"potner/station/{msg.data.lower()}/request"
        try:
            self._client.publish(topic, msg.data)
            self.get_logger().info(f"스테이션 요청 전송: {topic}")
        except Exception as exc:
            self.get_logger().error(f"스테이션 요청 발행 실패: {exc}")

    def _upload(self):
        if self._client is None or not self._latest:
            return

        topic = f"potner/{self._device_id}/telemetry"
        try:
            self._client.publish(topic, json.dumps(self._latest))
        except Exception as exc:
            self.get_logger().error(f"MQTT 발행 실패: {exc}")


def main(args=None):
    rclpy.init(args=args)
    node = MqttBridge()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
