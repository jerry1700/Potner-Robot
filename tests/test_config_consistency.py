"""설정 파일과 코드 기본값이 어긋나지 않는지 확인합니다.

같은 값이 세 곳에 흩어져 있습니다.

    1. potner_params.yaml       실제 운영에 쓰이는 값
    2. 노드의 declare_parameter  yaml 없이 단독 실행할 때의 값
    3. 순수 로직 dataclass       테스트와 라이브러리 기본값

셋이 어긋나도 launch 로 띄우면 yaml 이 이기기 때문에 아무 증상이
없습니다. 그러다 ros2 run 으로 노드 하나만 띄워 시험할 때 낡은 값이
조용히 쓰여서, 왜 거리가 안 맞는지 한참 헤매게 됩니다.

실제로 바퀴 지름을 75mm 로 확정한 뒤 yaml 만 고치고 노드 기본값은
0.065 로 남아 있던 적이 있어서 이 테스트를 넣었습니다.
"""

import re
from pathlib import Path

import pytest
import yaml

from potner_base.audio_queue import MAX_PENDING
from potner_base.kinematics import DriveConfig
from potner_base.speech import DEFAULT_AUDIO_COMMAND, DEFAULT_COMMAND
from potner_docking.approach_controller import DockingGains
from potner_perception.marker_pose import DEFAULT_FOCAL_LENGTH_PX, DEFAULT_MARKER_SIZE

REPO = Path(__file__).resolve().parent.parent
PARAMS = REPO / "src" / "potner_bringup" / "config" / "potner_params.yaml"

# 노드가 declare_parameter 기본값으로 쓰는 상수들. 노드 파일은 rclpy 를
# import 하므로 CI 에서 불러올 수 없어서, 상수가 사는 순수 모듈에서 직접
# 가져와 eval 이름공간에 넣습니다.
NODE_CONSTANTS = {
    "DEFAULT_MARKER_SIZE": DEFAULT_MARKER_SIZE,
    "DEFAULT_FOCAL_LENGTH_PX": DEFAULT_FOCAL_LENGTH_PX,
    "DEFAULT_COMMAND": DEFAULT_COMMAND,
    "DEFAULT_AUDIO_COMMAND": DEFAULT_AUDIO_COMMAND,
    "MAX_PENDING": MAX_PENDING,
}


def load_params():
    with open(PARAMS, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def node_defaults(package, module, node_name):
    """노드 소스에서 declare_parameter 기본값을 뽑아냅니다."""
    path = REPO / "src" / package / package / f"{module}.py"
    source = path.read_text(encoding="utf-8")

    defaults = {}
    # 기본값에 한 겹의 중첩 괄호까지 허용합니다 — list(DEFAULT_COMMAND) 처럼
    # 상수를 감싼 형태가 흔합니다. 이걸 못 읽으면 그 파라미터는 조용히
    # 대조에서 빠지고, 어긋나도 아무 증상이 없습니다.
    pattern = r'declare_parameter\(\s*"([^"]+)",\s*((?:[^,()]|\([^()]*\))+?)\s*\)'
    for name, literal in re.findall(pattern, source):
        try:
            defaults[name] = eval(literal, dict(NODE_CONSTANTS))
        except (SyntaxError, NameError, ValueError, TypeError):
            continue  # 표현식으로 된 기본값은 대조 대상이 아닙니다
    return defaults


NODES = [
    ("base_driver", "potner_base", "base_driver_node"),
    ("plant_sensors", "potner_base", "plant_sensors_node"),
    ("speaker", "potner_base", "speaker_node"),
    ("face_display", "potner_base", "face_display_node"),
    ("safety", "potner_mission", "safety_node"),
    ("mission_manager", "potner_mission", "mission_manager_node"),
    ("simple_navigator", "potner_mission", "simple_navigator_node"),
    ("pose_report", "potner_mission", "pose_report_node"),
    ("docking_server", "potner_docking", "docking_server_node"),
    ("marker_detector", "potner_perception", "marker_detector_node"),
    ("person_detector", "potner_perception", "person_detector_node"),
    ("scan_presence", "potner_perception", "scan_presence_node"),
    ("mqtt_bridge", "potner_bridge", "mqtt_bridge_node"),
]


@pytest.mark.parametrize("node_name,package,module", NODES)
def test_노드_기본값이_설정파일과_일치한다(node_name, package, module):
    params = load_params().get(node_name, {}).get("ros__parameters", {})
    defaults = node_defaults(package, module, node_name)

    mismatched = []
    for name, expected in params.items():
        if isinstance(expected, dict) or name not in defaults:
            continue
        actual = defaults[name]
        if isinstance(expected, float) or isinstance(actual, float):
            if abs(float(actual) - float(expected)) > 1e-9:
                mismatched.append(f"{name}: 코드 {actual} / yaml {expected}")
        elif actual != expected:
            mismatched.append(f"{name}: 코드 {actual!r} / yaml {expected!r}")

    assert not mismatched, f"{node_name} 기본값 불일치 — " + ", ".join(mismatched)


def test_기구학_기본값이_설정파일과_일치한다():
    params = load_params()["base_driver"]["ros__parameters"]
    cfg = DriveConfig()

    assert cfg.wheel_diameter == pytest.approx(params["wheel_diameter"])
    assert cfg.wheel_separation == pytest.approx(params["wheel_separation"])
    assert cfg.counts_per_rev == params["counts_per_rev"]
    assert cfg.max_wheel_speed == pytest.approx(params["max_wheel_speed"])


def test_도킹_게인_기본값이_설정파일과_일치한다():
    params = load_params()["docking_server"]["ros__parameters"]
    gains = DockingGains()

    for name in (
        "kp_lateral",
        "kp_yaw",
        "approach_speed",
        "max_angular",
        "target_distance",
        "aim_offset_px_per_deg",
        "aim_offset_distance",
        "aim_offset_max_px",
        "edge_guard_px",
        "edge_min_speed_ratio",
        "turn_after_dock_deg",
        "turn_speed",
        "turn_slow_angle_deg",
        "turn_min_speed",
        "turn_stop_margin_deg",
    ):
        assert getattr(gains, name) == pytest.approx(params[name]), name


def test_도킹_시한_기본값이_설정파일과_일치한다():
    """SessionLimits 는 노드가 yaml 값으로 덮어쓰지만, 테스트와 시뮬레이션은
    dataclass 기본값으로 돈다. 두 세계가 다른 값을 보면 CI 에서 통과한
    동작이 실기에서 재현되지 않는다."""
    from potner_docking.session import SessionLimits

    params = load_params()["docking_server"]["ros__parameters"]
    limits = SessionLimits()

    for name in (
        "marker_lost_timeout",
        "docking_timeout",
        "confirm_timeout",
        "search_timeout",
        "turn_timeout",
        "align_timeout",
    ):
        assert getattr(limits, name) == pytest.approx(params[name]), name
    assert limits.require_station_confirm == params["require_station_confirm"]


def test_간이_주행_기본값이_설정파일과_일치한다():
    from potner_mission.goto_controller import GotoConfig

    params = load_params()["simple_navigator"]["ros__parameters"]
    config = GotoConfig()

    for name in (
        "cruise_speed",
        "slow_distance",
        "min_speed",
        "kp_heading",
        "max_angular",
        "turn_speed",
        "turn_slow_angle_deg",
        "turn_min_speed",
        "position_tolerance",
        "yaw_tolerance_deg",
    ):
        assert getattr(config, name) == pytest.approx(params[name]), name
    # dataclass 쪽은 timeout, yaml·노드 쪽은 navigate_timeout 입니다.
    assert config.timeout == pytest.approx(params["navigate_timeout"])


def test_간이_주행이_임무관리자보다_먼저_포기한다():
    """반대로 두면 클라이언트가 먼저 포기해서, 왜 못 갔는지가 로그에
    안 남습니다. 주행이 실패한 이유는 주행 쪽이 알고 있습니다."""
    params = load_params()

    assert (
        params["simple_navigator"]["ros__parameters"]["navigate_timeout"]
        < params["mission_manager"]["ros__parameters"]["navigate_timeout"]
    )


def test_목적지_좌표는_로봇_설정에_두지_않는다():
    """좌표의 출처는 서버의 robot_location 하나입니다.

    로봇에도 좌표를 두면 지도를 다시 그렸을 때 한쪽만 갱신되어 엉뚱한
    곳으로 갑니다. 예전 station_poses 가 되살아나면 여기서 걸립니다.
    """
    params = load_params()["mission_manager"]["ros__parameters"]

    assert "station_poses" not in params
    for stale in ("battery_percent", "moisture_percent", "light_lux"):
        assert stale not in params, (
            f"{stale} 는 서버가 판단합니다. 로봇 임계값이 되살아났습니다."
        )


def test_감지_기본값이_설정파일과_일치한다():
    """라이다 사람 감지 임계값이 세 곳에 있습니다.

    PresenceConfig(라이브러리·테스트) / declare_parameter(단독 실행) /
    yaml(운영). 실기에서 튜닝하다 보면 yaml 만 고치기 쉬운데, 그러면
    CI 테스트는 낡은 dataclass 값으로 통과해 버립니다.
    """
    from potner_perception.scan_presence import PresenceConfig

    params = load_params()["scan_presence"]["ros__parameters"]
    config = PresenceConfig()

    for name, expected in params.items():
        actual = getattr(config, name)
        if isinstance(expected, float) or isinstance(actual, float):
            assert float(actual) == pytest.approx(float(expected)), name
        else:
            assert actual == expected, name


def test_감지_하한은_안전정지_해제선_바깥이다():
    """safety 가 막혔다가 풀리는 선(0.25 + 히스테리시스 0.08 = 0.33m)보다
    안쪽을 감지 대역으로 쓰면, 사람을 인식하는 순간이 곧 비상정지가 걸린
    순간이라 HOME 복귀 주행이 막힙니다."""
    from potner_perception.scan_presence import PresenceConfig

    safety = load_params()["safety"]["ros__parameters"]
    clear_line = safety["scan_stop_distance"] + safety["clear_hysteresis"]

    assert PresenceConfig().min_range_m > clear_line


def test_마커_크기가_설정파일과_일치한다():
    params = load_params()["marker_detector"]["ros__parameters"]
    assert DEFAULT_MARKER_SIZE == pytest.approx(params["marker_size"])


def test_초점거리가_설정파일과_일치한다():
    """보정한 값이 yaml 과 코드 기본값 두 곳에 있습니다.

    한쪽만 고치면 launch 로 띄울 때는 멀쩡하고 ros2 run 으로 노드만 띄울
    때만 거리가 틀어져서, 원인을 찾기 어렵습니다.
    """
    from potner_perception.marker_pose import CameraIntrinsics

    params = load_params()["marker_detector"]["ros__parameters"]
    assert CameraIntrinsics().focal_length_px == pytest.approx(
        params["focal_length_px"]
    )


def test_서버로_보내는_상태_목록이_임무_상태_머신과_일치한다():
    """mission_manager 의 MissionState 이름을 그대로 MQTT 로 실어 보냅니다.

    한쪽에만 상태를 추가하면 그 상태에 들어간 순간 발행이 예외로 막힙니다.
    mission_manager 는 rclpy 를 import 해서 CI 에서 못 불러오므로 소스를
    읽어 대조합니다.
    """
    from potner_bridge.telemetry import ROBOT_STATES

    source = (
        REPO
        / "src"
        / "potner_mission"
        / "potner_mission"
        / "mission_manager_node.py"
    ).read_text(encoding="utf-8")

    block = re.search(
        r"class MissionState\(Enum\):(.*?)(?=\nclass |\Z)", source, re.DOTALL
    )
    assert block, "MissionState 정의를 찾지 못했습니다"

    states = set(re.findall(r"^\s+([A-Z_]+)\s*=\s*auto\(\)", block.group(1), re.M))

    assert states == ROBOT_STATES, (
        f"임무 상태 머신 {sorted(states)} 와 "
        f"서버 전송 목록 {sorted(ROBOT_STATES)} 가 다릅니다"
    )


def test_엔코더_분해능은_쿼드러처_4배수를_포함한_실측값이다():
    """사양서 1440 CPR 은 채널당 사이클이라 그대로 쓰면 4배 틀린다.

    ESP32Encoder 의 attachFullQuad() 가 한 사이클을 4카운트로 세므로 실제
    분해능은 5760 이다. 손으로 한 바퀴 돌려 5,941카운트(손 오차 3%)로
    확인했다. 1440 으로 되돌리면 펌웨어가 속도를 4배로 착각해 로봇이
    명령의 1/4 속도로만 움직인다.
    """
    assert DriveConfig().counts_per_rev == 5760


def _tf_child_frame():
    """base_driver 가 오도메트리 TF 의 자식으로 발행하는 프레임 이름."""
    source = (
        REPO / "src" / "potner_base" / "potner_base" / "base_driver_node.py"
    ).read_text(encoding="utf-8")
    match = re.search(r'tf\.child_frame_id\s*=\s*"([^"]+)"', source)
    assert match, "base_driver 의 TF 자식 프레임을 찾지 못했습니다"
    return match.group(1)


def test_오도메트리_TF_자식은_URDF_트리의_뿌리여야_한다():
    """URDF 가 이미 부모를 가진 프레임에 odom 을 또 붙이면 TF 가 갈라진다.

    base_link 는 URDF 에서 base_footprint 의 자식이다. 여기에 odom 을
    붙이면 base_link 에 부모가 둘이 되어 tf2 가 트리를 두 조각으로 쪼갠다.
    그러면 slam_toolbox 가 "Failed to compute odom pose" 를 쏟아내며 스캔을
    전부 버려서 **지도가 한 장도 만들어지지 않는다.** 실기에서 그렇게
    당했고, 원인을 찾는 데 오래 걸렸다.
    """
    child = _tf_child_frame()
    urdf = (
        REPO / "src" / "potner_description" / "urdf" / "potner.urdf.xacro"
    ).read_text(encoding="utf-8")

    parented = set(re.findall(r'<child\s+link="([^"]+)"', urdf))

    assert child not in parented, (
        f"base_driver 가 odom -> {child} 를 발행하는데 URDF 도 {child} 에 "
        f"부모를 붙이고 있습니다. TF 트리가 갈라집니다."
    )


def test_오도메트리_TF_자식이_SLAM과_Nav2_기준_프레임과_같다():
    """세 곳이 어긋나면 스캔이 지도에 얹히지 않는다."""
    child = _tf_child_frame()

    slam = yaml.safe_load(
        (REPO / "src" / "potner_bringup" / "config" / "slam_toolbox.yaml")
        .read_text(encoding="utf-8")
    )
    nav2 = yaml.safe_load(
        (REPO / "src" / "potner_bringup" / "config" / "nav2_params.yaml")
        .read_text(encoding="utf-8")
    )

    assert slam["slam_toolbox"]["ros__parameters"]["base_frame"] == child
    assert nav2["amcl"]["ros__parameters"]["base_frame_id"] == child
    for scope in ("local_costmap", "global_costmap"):
        params = nav2[scope][scope]["ros__parameters"]
        assert params["robot_base_frame"] == child, scope
