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

from potner_base.kinematics import DriveConfig
from potner_docking.approach_controller import DockingGains
from potner_mission.priority import Thresholds
from potner_perception.marker_pose import DEFAULT_FOCAL_LENGTH_PX, DEFAULT_MARKER_SIZE

REPO = Path(__file__).resolve().parent.parent
PARAMS = REPO / "src" / "potner_bringup" / "config" / "potner_params.yaml"


def load_params():
    with open(PARAMS, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def node_defaults(package, module, node_name):
    """노드 소스에서 declare_parameter 기본값을 뽑아냅니다."""
    path = REPO / "src" / package / package / f"{module}.py"
    source = path.read_text(encoding="utf-8")

    defaults = {}
    pattern = r'declare_parameter\(\s*"([^"]+)",\s*([^,)]+?)\s*\)'
    for name, literal in re.findall(pattern, source):
        try:
            defaults[name] = eval(
                literal,
                {
                    "DEFAULT_MARKER_SIZE": DEFAULT_MARKER_SIZE,
                    "DEFAULT_FOCAL_LENGTH_PX": DEFAULT_FOCAL_LENGTH_PX,
                },
            )
        except (SyntaxError, NameError, ValueError):
            continue  # 표현식으로 된 기본값은 대조 대상이 아닙니다
    return defaults


NODES = [
    ("base_driver", "potner_base", "base_driver_node"),
    ("plant_sensors", "potner_base", "plant_sensors_node"),
    ("speaker", "potner_base", "speaker_node"),
    ("safety", "potner_mission", "safety_node"),
    ("mission_manager", "potner_mission", "mission_manager_node"),
    ("docking_server", "potner_docking", "docking_server_node"),
    ("marker_detector", "potner_perception", "marker_detector_node"),
    ("person_detector", "potner_perception", "person_detector_node"),
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

    assert gains.kp_lateral == pytest.approx(params["kp_lateral"])
    assert gains.kp_yaw == pytest.approx(params["kp_yaw"])
    assert gains.approach_speed == pytest.approx(params["approach_speed"])
    assert gains.max_angular == pytest.approx(params["max_angular"])
    assert gains.target_distance == pytest.approx(params["target_distance"])


def test_임무_임계값이_설정파일과_일치한다():
    params = load_params()["mission_manager"]["ros__parameters"]
    thresholds = Thresholds()

    assert thresholds.battery_percent == pytest.approx(params["battery_percent"])
    assert thresholds.moisture_percent == pytest.approx(params["moisture_percent"])
    assert thresholds.light_lux == pytest.approx(params["light_lux"])
    assert thresholds.temperature_celsius == pytest.approx(
        params["temperature_celsius"]
    )


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


def test_엔코더_분해능은_부품_사양값이다():
    """FIT0403 출력축 1440 CPR. 부품을 바꾸지 않는 한 손대면 안 됩니다."""
    assert DriveConfig().counts_per_rev == 1440
