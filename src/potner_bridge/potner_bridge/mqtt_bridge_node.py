"""mqtt_bridge — Spring Boot 서버와 이어지는 유일한 창구.

ROS 2와 MQTT는 서로 다른 세계입니다. 로봇 내부는 ROS 토픽으로, 집 밖과는
MQTT로 이야기합니다. 이 노드가 그 사이를 번역합니다.

    올려보냄   센서 측정값 (측정값 하나당 메시지 하나), 하트비트,
               로봇 상태, 배터리 잔량, 명령 결과 회신
    내려받음   표정, 귀가 마중(welcome_start/cancel), 이동(navigate),
               수동 주행(drive — 회신 없음, drive_contract.py 참고)

명령을 여기서 직접 수행하지 않습니다. 검증만 하고 ROS 토픽으로 넘긴 뒤
mission_manager 의 결과를 받아 서버로 되돌립니다. Nav2 목표의 주인이
둘이 되면 서로를 취소하기 때문입니다.

메시지 형식은 potner_bridge.telemetry(측정값)와 command_result·
arrival_contract·navigate_contract(명령·결과)에 모아뒀습니다. 기준 문서는
docs/DEVICE-MQTT.md 이고 서버 팀이 관리합니다.

브로커 ACL 이 접근 범위를 정합니다 (DEVICE-MQTT.md 3절).

    pattern write potner/device/%u/sensor|status|result/#
    pattern read  potner/device/%u/command/#

`%u` 는 접속 계정명입니다. 그래서 **자기 device_uid 아래만** 오갈 수 있고,
다른 기기의 토픽이나 potner/station/... 은 브로커가 거부합니다. 거부는
발행 쪽에 에러로 돌아오지 않고 조용히 버려지므로 찾기 어렵습니다.

주의: 이 노드가 죽거나 인터넷이 끊겨도 주행과 안전 정지는 계속 돌아야
합니다. 그래서 Nav2나 safety 노드는 여기에 전혀 의존하지 않습니다.
"""

import os

import rclpy
from rclpy.node import Node
from std_msgs.msg import Float32, String

from potner_bridge.arrival_contract import (
    WELCOME_CANCEL,
    WELCOME_START,
    ArrivalCommandError,
    arrival_command_json,
    parse_arrival_command,
)
from potner_bridge.command_result import (
    NAVIGATE,
    CommandError,
    parse_result_envelope,
    result_message,
    result_topic,
)
from potner_bridge.drive_contract import (
    DRIVE,
    drive_command_json,
    parse_drive_command,
)
from potner_bridge.navigate_contract import (
    navigate_command_json,
    parse_navigate_command,
)
from potner_bridge.sensor_window import SensorWindow
from potner_bridge.telemetry import (
    SensorType,
    battery_message,
    battery_topic,
    command_topic,
    heartbeat_message,
    heartbeat_topic,
    now_utc,
    parse_expression_command,
    sensor_message,
    sensor_topic,
    state_message,
    state_topic,
)

try:
    import paho.mqtt.client as mqtt
except ImportError:
    mqtt = None

# ROS 토픽 -> 서버 SensorType 대응.
# 온도와 습도는 스테이션이 재서 서버로 직접 올립니다.
# 배터리는 센서가 아니라 전용 토픽으로 갑니다 (DEVICE-MQTT.md 8절).
SENSOR_SOURCES = (
    ("plant/moisture", SensorType.SOIL_MOISTURE),
    ("plant/lux", SensorType.ILLUMINANCE),
)


class MqttBridge(Node):
    def __init__(self):
        super().__init__("mqtt_bridge")

        self.declare_parameter("broker_host", "i15e104.p.ssafy.io")
        self.declare_parameter("broker_port", 1884)
        self.declare_parameter("device_id", "jetson-01")
        # 비밀번호는 저장소에 두지 않습니다. 환경변수 이름만 설정에 적고
        # 값은 젯슨의 셸 환경에서 읽습니다.
        self.declare_parameter("password_env", "POTNER_MQTT_PASSWORD")
        self.declare_parameter("publish_period", 10.0)
        self.declare_parameter("heartbeat_period", 30.0)
        self.declare_parameter("battery_period", 60.0)

        self._device_id = self.get_parameter("device_id").value
        self._sensor_topic = sensor_topic(self._device_id)
        self._heartbeat_topic = heartbeat_topic(self._device_id)
        self._state_topic = state_topic(self._device_id)
        self._battery_topic = battery_topic(self._device_id)

        # 센서 종류별로 발행 주기 동안의 측정값을 모읍니다. 새 값으로
        # 덮어쓰면 2초 주기로 잰 5개 중 4개가 버려지고, 서버는 살아남은
        # 순간값 하나를 10초 내내 유지된 것으로 적분합니다 — 잠깐 튄 값이
        # 10초치 광량이 됩니다. sensor_window.py 머리말 참고.
        self._windows = {
            sensor_type: SensorWindow() for _topic, sensor_type in SENSOR_SOURCES
        }
        # 창을 닫아 만든 발행 대기값. measuredAt 은 발행 시각이 아니라
        # 실제로 읽은 시각(그 창의 첫 표본 시각)입니다.
        self._latest = {}
        self._battery = None
        self._state = None
        self._state_changed_at = None
        # 마지막으로 로그에 남긴 표정. 반복 수신과 실제 변화를 구분하는 데만
        # 씁니다 — 발행(_expression_pub)은 이것과 무관하게 매번 합니다.
        self._last_expression_log = None

        for topic, sensor_type in SENSOR_SOURCES:
            self.create_subscription(
                Float32, topic, self._capture(sensor_type), 10
            )

        self.create_subscription(
            Float32, "battery/percent", self._on_battery, 10
        )
        self.create_subscription(String, "mission/state", self._on_state, 10)

        # 서버가 정한 표정을 face_display 로 넘깁니다. 로봇은 표정을 판단하지
        # 않습니다 — 식물 상태를 아는 쪽이 서버라서 그쪽이 정합니다.
        self._expression_pub = self.create_publisher(String, "display/expression", 10)
        self._reason_pub = self.create_publisher(String, "display/reason", 10)
        # 서버의 welcome_start/cancel을 검증한 뒤 mission_manager에 전달합니다.
        # mission_manager 결과는 다시 서버 result/<command> 토픽으로 보냅니다.
        self._arrival_command_pub = self.create_publisher(
            String, "mission/arrival_command", 10
        )
        # 서버의 navigate도 같은 모양입니다. 자동 급수·촬영·말리기·햇빛
        # 이동 네 기능이 전부 이 명령으로 시작합니다.
        self._navigate_command_pub = self.create_publisher(
            String, "mission/navigate_command", 10
        )
        # 휴대폰 방향 버튼(수동 주행). navigate와 달리 회신 계약이 없어서
        # mission/*_result 구독에도 안 들어갑니다 — drive_node가 twist_mux
        # teleop 슬롯으로 바로 흘려보내고 끝입니다.
        self._drive_command_pub = self.create_publisher(
            String, "mission/drive_command", 10
        )
        # 결과 봉투에 commandName이 들어 있어 두 토픽을 한 콜백으로 받습니다.
        for topic in ("mission/arrival_result", "mission/navigate_result"):
            self.create_subscription(String, topic, self._on_command_result, 10)

        self._command_topic = command_topic(self._device_id)
        self._client = self._connect()
        self.create_timer(
            self.get_parameter("publish_period").value, self._publish_sensors
        )
        self.create_timer(
            self.get_parameter("heartbeat_period").value, self._publish_heartbeat
        )
        self.create_timer(
            self.get_parameter("battery_period").value, self._publish_battery
        )

    # --- MQTT ---

    def _connect(self):
        if mqtt is None:
            self.get_logger().warn("paho-mqtt 미설치 — 브리지가 동작하지 않습니다.")
            return None

        host = self.get_parameter("broker_host").value
        port = self.get_parameter("broker_port").value

        # 브로커는 익명 접속을 받지 않습니다. 계정명은 device_uid 와 같아야
        # ACL 의 %u 치환이 자기 토픽을 가리킵니다.
        env_name = self.get_parameter("password_env").value
        password = os.environ.get(env_name)
        if not password:
            self.get_logger().error(
                f"{env_name} 환경변수가 없습니다 — 브로커가 접속을 거부합니다. "
                f"~/.bashrc 에 export {env_name}='...' 를 넣으세요."
            )
            return None

        # client_id 는 브로커에서 유일해야 합니다. 겹치면 서로를 계속 끊어냅니다.
        # 서버가 potner-backend-prod 계열을 쓰므로 기기 이름에 접미사를 붙입니다.
        client = self._new_client(f"{self._device_id}-bridge")
        client.username_pw_set(self._device_id, password)
        client.on_connect = self._on_connect
        client.on_message = self._on_message

        try:
            client.connect(host, port, keepalive=60)
            client.loop_start()
            self.get_logger().info(
                f"MQTT 연결 시도: {host}:{port} (deviceId={self._device_id})"
            )
        except Exception as exc:
            # 인터넷이 없어도 로봇 자체는 돌아야 하므로 죽지 않습니다.
            self.get_logger().error(f"MQTT 연결 실패: {exc}")
            return None
        return client

    def _new_client(self, client_id):
        """paho 1.x / 2.x 를 함께 지원합니다.

        2.x 는 CallbackAPIVersion 을 첫 인자로 요구합니다. 생략해도 지금은
        경고만 내고 VERSION1 로 동작하지만, 다음 major 에서 끊길 자리입니다.
        젯슨의 apt 패키지가 1.x 라 양쪽을 다 받아둡니다.
        """
        if hasattr(mqtt, "CallbackAPIVersion"):
            return mqtt.Client(
                mqtt.CallbackAPIVersion.VERSION2, client_id=client_id
            )
        return mqtt.Client(client_id=client_id)

    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        """접속 성공 여부를 남깁니다.

        인증 실패는 connect() 가 아니라 여기서 드러납니다. connect() 는
        TCP 연결까지만 보고 돌아오기 때문입니다. rc=5 가 계정·비밀번호
        오류이고, 이걸 로그로 안 남기면 "연결됨" 만 보고 값이 안 들어오는
        이유를 못 찾습니다.
        """
        # VERSION2 는 ReasonCode 객체, VERSION1 은 int 를 넘깁니다.
        code = getattr(reason_code, "value", reason_code)
        if code != 0:
            self.get_logger().error(
                f"MQTT 접속 거부 (rc={code}) — 계정명이 device_id 와 같은지, "
                f"비밀번호가 맞는지 확인하세요."
            )
            return

        self.get_logger().info("MQTT 인증 성공")

        # 재접속마다 다시 구독해야 합니다. clean session 이라 브로커에 구독이
        # 남지 않아서, 이걸 setup 에서 한 번만 하면 재접속 뒤로 표정이 끊깁니다.
        client.subscribe(self._command_topic, qos=1)
        self.get_logger().info(f"명령 구독: {self._command_topic}")

    def _on_message(self, client, userdata, message):
        """서버가 보낸 명령. ACL 이 command/# 만 읽기 허용합니다."""
        command_name = message.topic.rsplit("/", 1)[-1]
        if command_name == NAVIGATE:
            try:
                command = parse_navigate_command(message.payload)
            except CommandError as exc:
                self.get_logger().error(
                    f"이동 명령 거부: code={exc.code}, reason={exc}"
                )
                if exc.request_id is not None:
                    # 거절도 회신해야 서버가 제한시간까지 기다리지 않습니다.
                    self._publish_command_result(
                        NAVIGATE,
                        exc.request_id,
                        "ERROR",
                        error=str(exc),
                        code=exc.code,
                    )
                return

            self._navigate_command_pub.publish(
                String(data=navigate_command_json(command))
            )
            self.get_logger().info(
                f"이동 명령 수신 -> mission/navigate_command "
                f"(destination={command.destination}, "
                f"requestId={command.request_id})"
            )
            return

        if command_name == DRIVE:
            try:
                command = parse_drive_command(message.payload)
            except CommandError as exc:
                # drive는 회신 계약이 없습니다(서버가 result/drive를 인식하지
                # 않음) — 거절해도 회신을 만들지 않고 로그만 남깁니다.
                self.get_logger().error(
                    f"주행 명령 거부: code={exc.code}, reason={exc}"
                )
                return

            self._drive_command_pub.publish(
                String(data=drive_command_json(command))
            )
            self.get_logger().info(
                f"주행 명령 수신 -> mission/drive_command "
                f"(direction={command.direction}, "
                f"requestId={command.request_id})"
            )
            return

        if command_name in {WELCOME_START, WELCOME_CANCEL}:
            try:
                command = parse_arrival_command(command_name, message.payload)
            except ArrivalCommandError as exc:
                self.get_logger().error(
                    f"귀가 명령 거부: command={command_name}, code={exc.code}, "
                    f"reason={exc}"
                )
                if exc.request_id is not None:
                    self._publish_command_result(
                        command_name,
                        exc.request_id,
                        "ERROR",
                        error=str(exc),
                        code=exc.code,
                    )
                return

            self._arrival_command_pub.publish(
                String(data=arrival_command_json(command))
            )
            self.get_logger().info(
                f"귀가 명령 수신 -> mission/arrival_command "
                f"(command={command_name}, visitId={command.visit_id})"
            )
            return

        if not message.topic.endswith("/command/expression"):
            # 서버가 명령을 늘렸습니다. 무시하되 남겨서 알 수 있게 합니다.
            self.get_logger().info(
                f"처리하지 않는 명령: {message.topic}", throttle_duration_sec=60.0
            )
            return

        expression, reason = parse_expression_command(message.payload)
        self._expression_pub.publish(String(data=expression))
        self._reason_pub.publish(String(data=reason or ""))

        # ★ throttle_duration_sec 은 "이 로그 호출이 몇 초에 한 번만 찍히는가"
        # 를 정할 뿐, 그 사이에 값이 바뀌었는지는 안 봅니다. 예전엔 이 로그
        # 전체에 120초 throttle 을 걸어서, 실제로 VERY_HAPPY 가 왔다 가도
        # 그 사이에 있으면 로그에 안 남고 조용히 다음 SAD 로 넘어갔습니다.
        # 그래서 변화는 즉시 남기고, 같은 값의 반복만 뜸하게 남깁니다 —
        # "표정이 NEUTRAL 로 고정된 채 계속 도착 중"이라는 사실도 여전히
        # 알 수 있어야 하기 때문입니다.
        current = (expression, reason)
        if current != self._last_expression_log:
            self._last_expression_log = current
            self.get_logger().info(f"표정 수신: {expression} (사유 {reason or '없음'})")
        else:
            self.get_logger().info(
                f"표정 반복 수신: {expression} (사유 {reason or '없음'})",
                throttle_duration_sec=120.0,
            )

    def _on_command_result(self, msg: String):
        """mission_manager 결과를 서버가 구독하는 MQTT 토픽으로 중계합니다."""
        try:
            result = parse_result_envelope(msg.data)
        except CommandError as exc:
            self.get_logger().error(f"내부 결과 거부: {exc}")
            return

        self._publish_command_result(
            result["commandName"],
            result["requestId"],
            result["status"],
            error=result["error"],
            code=result["code"],
        )

    def _publish_command_result(
        self,
        command_name,
        request_id,
        status,
        *,
        error=None,
        code=None,
    ):
        """결과를 ``result/<command>`` 로 보냅니다.

        토픽 세그먼트와 페이로드 deviceId 를 같은 변수에서 만듭니다. 두
        군데에 따로 적으면 대소문자가 어긋나 서버가 조용히 버립니다
        (DEVICE-MQTT.md 4절).
        """
        topic = result_topic(self._device_id, command_name)
        payload = result_message(
            self._device_id,
            request_id,
            status,
            error=error,
            code=code,
        )
        if self._publish(topic, payload, "명령 결과"):
            self.get_logger().info(
                f"명령 결과 발행: command={command_name}, "
                f"requestId={request_id}, status={status}"
            )

    # --- ROS ---

    def _capture(self, sensor_type):
        """센서값을 종류별로 창에 누적하는 콜백을 만듭니다."""

        def callback(msg):
            self._windows[sensor_type].add(float(msg.data), now_utc())

        return callback

    def _on_battery(self, msg: Float32):
        self._battery = (float(msg.data), now_utc())

    def _on_state(self, msg: String):
        """상태가 바뀔 때만 서버에 알립니다.

        mission_manager 는 판단 주기(2초)마다 같은 상태를 계속 발행합니다.
        서버 처리는 멱등해서 반복 발행도 받아주지만, 그대로 중계하면
        브로커에 쓸데없는 메시지가 쌓이므로 변화만 골라냅니다.
        """
        if msg.data == self._state:
            return

        self._state = msg.data
        self._state_changed_at = now_utc()
        self._publish_state()

    # --- 발행 ---

    def _publish(self, topic, payload, label):
        """QoS 1 로 보냅니다.

        QoS 0 은 브로커까지 도달을 보장하지 않습니다. 무선 구간이 있고
        측정 주기가 10초라 한 건 유실이 그대로 공백으로 남습니다.
        """
        if self._client is None:
            return False
        try:
            self._client.publish(topic, payload, qos=1)
            return True
        except Exception as exc:
            self.get_logger().error(
                f"{label} 발행 실패: {exc}", throttle_duration_sec=30.0
            )
            return False

    def _publish_sensors(self):
        """측정값 하나당 메시지 하나로 올려보냅니다.

        서버는 여러 센서를 묶은 메시지를 받지 않습니다. 한 번 보낸 값은
        지워서, 센서가 죽었을 때 같은 값을 계속 올리지 않게 합니다.
        """
        # 창은 클라이언트 상태와 무관하게 닫습니다. 끊긴 동안 열어두면 그
        # 시간 전체가 표본 하나의 평균이 되어, 짧은 구간의 변화가 뭉개진
        # 채로 긴 gap 에 곱해집니다.
        for sensor_type, window in self._windows.items():
            taken = window.take()
            if taken is not None:
                self._latest[sensor_type] = taken

        if self._client is None:
            return

        for sensor_type, (value, measured_at) in list(self._latest.items()):
            try:
                payload = sensor_message(
                    self._device_id, sensor_type, value, measured_at
                )
            except ValueError as exc:
                # 허용 범위를 벗어난 값입니다. 서버는 이걸 조용히 버리므로
                # 여기서 남겨야 센서 고장을 알 수 있습니다.
                self.get_logger().error(f"센서값 거부: {exc}")
                del self._latest[sensor_type]
                continue

            if self._publish(self._sensor_topic, payload, "센서"):
                del self._latest[sensor_type]
            # 실패면 값을 남겨둬서 다음 주기에 다시 시도합니다.

    def _publish_state(self):
        if self._state is None:
            return
        try:
            payload = state_message(
                self._device_id, self._state, self._state_changed_at
            )
        except ValueError as exc:
            # mission_manager 가 서버 enum 에 없는 상태를 만든 경우입니다.
            # 양쪽 enum 이 어긋났다는 신호라 조용히 넘기면 안 됩니다.
            self.get_logger().error(f"상태 메시지 생성 실패: {exc}")
            return
        self._publish(self._state_topic, payload, "상태")

    def _publish_battery(self):
        """배터리 잔량을 전용 토픽으로 보냅니다 (DEVICE-MQTT.md 8절).

        센서값보다 주기가 깁니다. 잔량은 분 단위로 변하고, 서버는 마지막
        값만 덮어쓰므로 자주 보낼 이유가 없습니다.
        """
        if self._battery is None:
            return

        percent, measured_at = self._battery
        try:
            payload = battery_message(self._device_id, percent, measured_at)
        except ValueError as exc:
            # 0~100 을 벗어났습니다. INA226 배선이나 전압 환산을 봐야 합니다.
            self.get_logger().error(f"배터리값 거부: {exc}")
            self._battery = None
            return

        self._publish(self._battery_topic, payload, "배터리")

    def _publish_heartbeat(self):
        """90초간 없으면 서버가 이 기기를 OFFLINE 으로 표시합니다."""
        self._publish(
            self._heartbeat_topic,
            heartbeat_message(self._device_id, now_utc()),
            "하트비트",
        )

        # 상태도 함께 다시 보냅니다. 변화 시점의 메시지가 유실되면 앱이
        # 낡은 상태를 계속 보여주는데, 이러면 30초 안에 복구됩니다.
        self._publish_state()


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
