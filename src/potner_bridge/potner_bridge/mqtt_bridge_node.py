"""mqtt_bridge — Spring Boot 서버 및 스테이션과 이어지는 유일한 창구.

ROS 2와 MQTT는 서로 다른 세계입니다. 로봇 내부는 ROS 토픽으로, 집 밖과는
MQTT로 이야기합니다. 이 노드가 그 사이를 번역합니다.

    올려보냄   센서 측정값 (측정값 하나당 메시지 하나), 하트비트
    내려받음   서버 LLM 대사, 스테이션 센서값, 스테이션 도착 신호

메시지 형식은 potner_bridge.telemetry 에 모아뒀습니다. 서버 enum 과 문자열이
정확히 일치해야 하고, 한 글자만 달라도 서버가 조용히 버립니다.

주의: 이 노드가 죽거나 인터넷이 끊겨도 주행과 안전 정지는 계속 돌아야
합니다. 그래서 Nav2나 safety 노드는 여기에 전혀 의존하지 않습니다.
"""

import rclpy
from rclpy.node import Node
from std_msgs.msg import Bool, Float32, String

from potner_bridge.telemetry import (
    SensorType,
    heartbeat_message,
    heartbeat_topic,
    now_utc,
    parse_sensor_message,
    sensor_message,
    sensor_topic,
)

try:
    import paho.mqtt.client as mqtt
except ImportError:
    mqtt = None

# ROS 토픽 -> 서버 SensorType 대응.
# 온도와 습도는 스테이션이 재서 올리므로 로봇은 발행하지 않습니다.
# battery/percent 는 INA226 이 재는 4S 젯슨팩입니다.
SENSOR_SOURCES = (
    ("plant/moisture", SensorType.SOIL_MOISTURE),
    ("plant/lux", SensorType.ILLUMINANCE),
    ("battery/percent", SensorType.BATTERY),
)

# 다른 기기가 올린 센서값을 함께 듣습니다. 로봇은 대기 온도를 직접 재지
# 않고 스테이션이 올린 값을 씁니다.
INBOUND_SENSOR_TOPIC = "potner/device/+/sensor/telemetry"


class MqttBridge(Node):
    def __init__(self):
        super().__init__("mqtt_bridge")

        self.declare_parameter("broker_host", "localhost")
        self.declare_parameter("broker_port", 1883)
        self.declare_parameter("device_id", "jetson-01")
        self.declare_parameter("publish_period", 10.0)
        self.declare_parameter("heartbeat_period", 30.0)

        self._device_id = self.get_parameter("device_id").value
        self._sensor_topic = sensor_topic(self._device_id)
        self._heartbeat_topic = heartbeat_topic(self._device_id)

        # 센서 종류별로 마지막 측정값과 측정 시각을 들고 있습니다.
        # measuredAt 은 발행 시각이 아니라 실제로 읽은 시각이어야 합니다.
        self._latest = {}

        for topic, sensor_type in SENSOR_SOURCES:
            self.create_subscription(
                Float32, topic, self._capture(sensor_type), 10
            )

        # 스테이션 서비스 요청은 모아 올리지 않고 즉시 중계합니다. 도킹이
        # 끝난 직후 급수나 송풍을 시작해야 하는데, 전송 주기를 기다리면
        # 로봇이 스테이션 앞에서 그만큼 멍하니 서 있게 됩니다.
        self.create_subscription(String, "station/request", self._on_request, 10)

        self._speech_pub = self.create_publisher(String, "tts/say", 10)
        self._station_pub = self.create_publisher(Bool, "station/docked", 10)
        # 대기 온도는 스테이션이 잽니다. 로봇에도 DHT11 이 있지만 리눅스에서
        # 원선 프로토콜을 읽는 건 마이크로초 타이밍이 필요해 자주 실패합니다.
        self._temperature_pub = self.create_publisher(Float32, "plant/temperature", 10)

        self._client = self._connect()
        self.create_timer(
            self.get_parameter("publish_period").value, self._publish_sensors
        )
        self.create_timer(
            self.get_parameter("heartbeat_period").value, self._publish_heartbeat
        )

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
            self.get_logger().info(
                f"MQTT 연결: {host}:{port} (deviceId={self._device_id})"
            )
        except Exception as exc:
            # 인터넷이 없어도 로봇 자체는 돌아야 하므로 죽지 않습니다.
            self.get_logger().error(f"MQTT 연결 실패: {exc}")
            return None
        return client

    def _on_connect(self, client, userdata, flags, rc):
        client.subscribe(f"potner/device/{self._device_id}/speech")
        client.subscribe("potner/station/+/docked")
        client.subscribe(INBOUND_SENSOR_TOPIC)

    def _on_message(self, client, userdata, message):
        topic = message.topic
        payload = message.payload.decode("utf-8", errors="ignore")

        if topic.endswith("/speech"):
            # 서버 LLM이 만든 대사. 음성 합성은 speaker 노드가 합니다.
            self._speech_pub.publish(String(data=payload))
        elif topic.endswith("/docked"):
            self._station_pub.publish(
                Bool(data=payload.strip() in ("1", "true", "True"))
            )
        elif topic.endswith("/sensor/telemetry"):
            self._on_inbound_sensor(topic, payload)

    def _on_inbound_sensor(self, topic: str, payload: str):
        """다른 기기가 올린 센서값 중 로봇에 필요한 것만 씁니다."""
        # 와일드카드로 구독했으므로 자기가 올린 메시지도 되돌아옵니다.
        if topic == self._sensor_topic:
            return

        sensor_type, value = parse_sensor_message(payload)
        if sensor_type == SensorType.TEMPERATURE:
            self._temperature_pub.publish(Float32(data=value))

    # --- ROS ---

    def _capture(self, sensor_type: str):
        """센서값을 종류별로 저장하는 콜백을 만듭니다."""

        def callback(msg):
            self._latest[sensor_type] = (float(msg.data), now_utc())

        return callback

    def _on_request(self, msg: String):
        """스테이션에 서비스 시작을 요청합니다 (급수, 송풍 등).

        ★ 토픽 이름을 스테이션 담당자(Raspberry-feature/mqtt-command-receiver)와
          맞춰야 합니다. 아직 확정되지 않았습니다.
        """
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

    # --- 발행 ---

    def _publish_sensors(self):
        """측정값 하나당 메시지 하나로 올려보냅니다.

        서버는 여러 센서를 묶은 메시지를 받지 않습니다. 한 번 보낸 값은
        지워서, 센서가 죽었을 때 같은 값을 계속 올리지 않게 합니다.
        """
        if self._client is None:
            return

        for sensor_type, (value, measured_at) in list(self._latest.items()):
            try:
                payload = sensor_message(
                    self._device_id, sensor_type, value, measured_at
                )
                self._client.publish(self._sensor_topic, payload)
                del self._latest[sensor_type]
            except ValueError as exc:
                self.get_logger().error(f"메시지 생성 실패: {exc}")
                del self._latest[sensor_type]
            except Exception as exc:
                # 발행 실패면 값을 남겨둬서 다음 주기에 다시 시도합니다.
                self.get_logger().error(
                    f"센서 발행 실패: {exc}", throttle_duration_sec=30.0
                )

    def _publish_heartbeat(self):
        if self._client is None:
            return
        try:
            self._client.publish(
                self._heartbeat_topic,
                heartbeat_message(self._device_id, now_utc()),
            )
        except Exception as exc:
            self.get_logger().error(
                f"하트비트 발행 실패: {exc}", throttle_duration_sec=30.0
            )


def main(args=None):
    rclpy.init(args=args)
    node = MqttBridge()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
