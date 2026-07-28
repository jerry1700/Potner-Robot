"""mission_manager — 로봇이 다음에 무엇을 할지 정하는 두뇌.

legacy/main.py 의 우선순위 스케줄러를 옮겼습니다. 가장 큰 차이는
**블로킹하지 않는다**는 점입니다. 기존 코드는 start_docking() 이 끝날
때까지 while 루프가 멈춰 있어서, 도킹 중에는 사용자가 귀가해도 반응할 수
없었습니다. 여기서는 Nav2와 도킹을 액션으로 보내고 결과를 콜백으로 받아,
기다리는 동안에도 다른 이벤트를 처리합니다.

임무 흐름:
    IDLE -> (센서가 기준을 넘음) -> NAVIGATING -> DOCKING -> SERVICING -> IDLE
                                  \\-> (사람 감지) -> GREETING -> IDLE
"""

from enum import Enum, auto

import rclpy
from rclpy.action import ActionClient
from rclpy.node import Node
from std_msgs.msg import Bool, Float32, String

from potner_mission.priority import Readings, StationMarker, Thresholds, evaluate

# TODO: potner_msgs 빌드 후 주석 해제
# from potner_msgs.action import DockToStation
# from nav2_msgs.action import NavigateToPose


class MissionState(Enum):
    IDLE = auto()
    NAVIGATING = auto()
    DOCKING = auto()
    SERVICING = auto()
    GREETING = auto()


class MissionManager(Node):
    def __init__(self):
        super().__init__("mission_manager")

        self.declare_parameter("battery_percent", 20.0)
        self.declare_parameter("moisture_percent", 30.0)
        self.declare_parameter("light_lux", 200.0)
        self.declare_parameter("temperature_celsius", 30.0)
        self.declare_parameter("evaluate_period", 2.0)
        self.declare_parameter("greeting_cooldown", 300.0)

        self.thresholds = Thresholds(
            battery_percent=self.get_parameter("battery_percent").value,
            moisture_percent=self.get_parameter("moisture_percent").value,
            light_lux=self.get_parameter("light_lux").value,
            temperature_celsius=self.get_parameter("temperature_celsius").value,
        )

        self.state = MissionState.IDLE
        self.readings = Readings()
        self._last_greeting = None

        # 식물 센서 (plant_sensors 노드가 발행)
        self.create_subscription(Float32, "plant/moisture", self._set("moisture"), 10)
        self.create_subscription(Float32, "plant/lux", self._set("light"), 10)
        self.create_subscription(Float32, "plant/temperature", self._set("temperature"), 10)
        self.create_subscription(Float32, "battery/percent", self._set("battery"), 10)

        # 사람 감지 (person_detector 노드가 발행)
        self.create_subscription(Bool, "perception/person_present", self._on_person, 10)

        # 스피커로 내보낼 대사. 실제 음성 합성은 서버가 하고 여기서는
        # 무엇을 말할지만 정합니다.
        self._speech_pub = self.create_publisher(String, "tts/say", 10)
        self._state_pub = self.create_publisher(String, "mission/state", 10)

        # TODO: potner_msgs 빌드 후 주석 해제
        # self._nav_client = ActionClient(self, NavigateToPose, "navigate_to_pose")
        # self._dock_client = ActionClient(self, DockToStation, "dock_to_station")

        period = self.get_parameter("evaluate_period").value
        self.create_timer(period, self._evaluate)

        self.get_logger().info("mission_manager 시작 (상태: IDLE)")

    # ------------------------------------------------------------------ 입력

    def _set(self, field):
        """Float32 토픽 값을 Readings 의 해당 필드에 넣는 콜백을 만듭니다."""

        def callback(msg):
            setattr(self.readings, field, msg.data)

        return callback

    def _on_person(self, msg: Bool):
        if not msg.data or self.state is not MissionState.IDLE:
            return

        now = self.get_clock().now()
        cooldown = self.get_parameter("greeting_cooldown").value
        if self._last_greeting is not None:
            if (now - self._last_greeting).nanoseconds * 1e-9 < cooldown:
                return

        self._last_greeting = now
        self._transition(MissionState.GREETING)

        # TODO: 서버 LLM이 생성한 페르소나 대사로 교체.
        # 지금은 네트워크 없이도 데모가 되도록 고정 문구를 씁니다.
        message = String()
        message.data = "다녀오셨어요? 오늘도 잘 지냈어요."
        self._speech_pub.publish(message)

        self._transition(MissionState.IDLE)

    # ------------------------------------------------------------------ 판단

    def _evaluate(self):
        self._state_pub.publish(String(data=self.state.name))

        if self.state is not MissionState.IDLE:
            return

        station = evaluate(self.readings, self.thresholds)
        if station is None:
            return

        self.get_logger().info("임무 발생: %s 스테이션으로 이동" % station.name)
        self._start_mission(station)

    def _start_mission(self, station: StationMarker):
        """Nav2로 스테이션 근처까지 간 뒤, 도킹 액션에 최종 접근을 넘깁니다.

        역할을 나눈 이유가 있습니다. Nav2는 지도상의 좌표까지 데려다주는
        데는 뛰어나지만 오차가 수십 cm 단위입니다. 충전 단자를 맞추려면
        cm 단위 정밀도가 필요해서, 마지막 구간은 마커를 보는 전용
        컨트롤러가 맡습니다.
        """
        self._transition(MissionState.NAVIGATING)

        # TODO: potner_msgs 와 지도 좌표가 준비되면 구현.
        #   1. config 에서 station 별 목표 좌표를 읽어 NavigateToPose 전송
        #   2. 성공 콜백에서 DockToStation(marker_id=station) 전송
        #   3. 도킹 성공 시 SERVICING -> 서비스 완료 대기 -> IDLE
        self.get_logger().warn(
            "이동/도킹 액션이 아직 연결되지 않았습니다 (station=%s)" % station.name
        )
        self._transition(MissionState.IDLE)

    def _transition(self, new_state: MissionState):
        if new_state is self.state:
            return
        self.get_logger().info("상태 전이: %s -> %s" % (self.state.name, new_state.name))
        self.state = new_state
        self._state_pub.publish(String(data=self.state.name))


def main(args=None):
    rclpy.init(args=args)
    node = MissionManager()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
