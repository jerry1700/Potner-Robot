#!/usr/bin/env python3
"""시연 조종기 - 좌표를 기억하고 명령을 보냅니다.

손으로 JSON 을 만들거나 requestId 를 바꾸거나 임무가 끝났는지 지켜볼
필요가 없습니다. 그 셋이 실기에서 계속 사고를 냈습니다 - 같은 id 를
다시 써서 명령이 조용히 무시되고, 앞 임무가 안 끝난 채로 다음을 보내
BUSY 로 거부되고, 좌표를 잘못 옮겨 적었습니다.

    python3 tools/demo.py where              지금 좌표 보기
    python3 tools/demo.py save home          지금 자리를 HOME 으로 기억
    python3 tools/demo.py save home 0 0 0    좌표를 직접 지정 (원점 = 띄운 자리)
    python3 tools/demo.py save station       (스테이션 정면 50cm, 마커 정면)
    python3 tools/demo.py save greeting
    python3 tools/demo.py save sun
    python3 tools/demo.py list                기억한 좌표 보기

    python3 tools/demo.py go home             HOME 으로 이동
    python3 tools/demo.py go station          이동 + 정밀 도킹
    python3 tools/demo.py welcome             마중 (greeting 갔다가 home 복귀)

★ 좌표는 오도메트리 원점 기준입니다. launch 를 다시 띄우면 원점이 그
  자리로 새로 잡히므로, 바닥에 표시해 둔 원점에 로봇을 놓고 띄우세요.
  다른 자리에서 띄웠으면 좌표를 다시 저장해야 합니다.
"""

import json
import os
import sys
import time

STORE = os.path.expanduser("~/.potner_demo_poses.json")

# 사람이 쓰는 짧은 이름 -> 서버가 쓰는 목적지 이름
PLACES = {
    "home": "HOME",
    "station": "WATER_STATION",
    "greeting": "GREETING",
    "sun": "SUNLIGHT",
}


def load():
    if not os.path.exists(STORE):
        return {}
    with open(STORE, encoding="utf-8") as fh:
        return json.load(fh)


def store(poses):
    with open(STORE, "w", encoding="utf-8") as fh:
        json.dump(poses, fh, ensure_ascii=False, indent=2)


def describe(name, pose):
    import math

    return (
        f"  {name:9s} x={pose['x']:+.3f}  y={pose['y']:+.3f}  "
        f"yaw={math.degrees(pose['yaw']):+.0f}도"
    )


class Robot:
    """ROS 연결. 필요할 때만 만듭니다."""

    def __init__(self):
        import rclpy
        from nav_msgs.msg import Odometry
        from rclpy.node import Node
        from std_msgs.msg import String

        rclpy.init()
        self._rclpy = rclpy
        self.node = Node("potner_demo")
        self.pose = None
        self.state = None
        self.results = []

        self.node.create_subscription(Odometry, "odom", self._on_odom, 10)
        self.node.create_subscription(String, "mission/state", self._on_state, 10)
        for topic in ("mission/navigate_result", "mission/arrival_result"):
            self.node.create_subscription(
                String, topic, self._on_result, 10
            )
        self._nav_pub = self.node.create_publisher(
            String, "mission/navigate_command", 10
        )
        self._arrival_pub = self.node.create_publisher(
            String, "mission/arrival_command", 10
        )

    def _on_odom(self, msg):
        import math

        q = msg.pose.pose.orientation
        self.pose = {
            "x": round(msg.pose.pose.position.x, 3),
            "y": round(msg.pose.pose.position.y, 3),
            "yaw": round(
                math.atan2(
                    2.0 * (q.w * q.z + q.x * q.y),
                    1.0 - 2.0 * (q.y * q.y + q.z * q.z),
                ),
                3,
            ),
        }

    def _on_state(self, msg):
        self.state = msg.data

    def _on_result(self, msg):
        try:
            self.results.append(json.loads(msg.data))
        except json.JSONDecodeError:
            pass

    def spin(self, seconds):
        end = time.monotonic() + seconds
        while self._rclpy.ok() and time.monotonic() < end:
            self._rclpy.spin_once(self.node, timeout_sec=0.1)

    def wait_for_pose(self, seconds=8.0):
        end = time.monotonic() + seconds
        while self._rclpy.ok() and time.monotonic() < end:
            self._rclpy.spin_once(self.node, timeout_sec=0.1)
            if self.pose is not None:
                return True
        return False

    def wait_until_free(self, seconds=180.0):
        """앞 임무가 끝나기를 기다립니다.

        IDLE 과 SERVICING 만 새 명령을 받습니다 (mission_manager
        _ready_for_server_command). 여기서 안 기다리면 BUSY 로 거부됩니다.
        """
        end = time.monotonic() + seconds
        announced = False
        while self._rclpy.ok() and time.monotonic() < end:
            self._rclpy.spin_once(self.node, timeout_sec=0.1)
            if self.state in ("IDLE", "SERVICING"):
                return True
            if self.state is not None and not announced:
                announced = True
                print(f"  앞 임무가 진행 중입니다 ({self.state}). 기다리는 중...")
        return False

    def send(self, publisher, body, request_id, label, timeout):
        self.results.clear()
        publisher.publish(_string(json.dumps(body, ensure_ascii=False)))
        print(f"\n{label} 명령을 보냈습니다 (id={request_id}).")

        end = time.monotonic() + timeout
        shown = self.state
        started = time.monotonic()
        while self._rclpy.ok() and time.monotonic() < end:
            self._rclpy.spin_once(self.node, timeout_sec=0.1)
            if self.state != shown:
                shown = self.state
                print(f"  [{time.monotonic() - started:5.1f}초] {shown}")
            for result in self.results:
                if result.get("requestId") == request_id:
                    status = result.get("status", "?")
                    mark = "성공" if status in ("OK", "SKIPPED") else "실패"
                    print(f"\n>>> {mark} - status={status}")
                    if result.get("error"):
                        print(f"    사유: {result['error']}")
                    return status in ("OK", "SKIPPED")
        print(f"\n>>> {timeout:.0f}초 안에 회신이 없습니다. 로그를 보세요.")
        print("    tail -30 /tmp/robot.log")
        return False

    def close(self):
        self.node.destroy_node()
        if self._rclpy.ok():
            self._rclpy.shutdown()


def _string(data):
    from std_msgs.msg import String

    return String(data=data)


def next_id(prefix):
    """매번 다른 id. 같은 id 를 다시 쓰면 재전송으로 보고 무시됩니다."""
    poses = load()
    count = poses.get("_counter", 0) + 1
    poses["_counter"] = count
    store(poses)
    return f"{prefix}{count}"


def cmd_where(robot):
    if not robot.wait_for_pose():
        print("오도메트리가 없습니다. base_driver 가 떴는지 확인하세요.")
        return 1
    print(describe("지금", robot.pose))
    return 0


def cmd_save(robot, name, explicit=None):
    """지금 자리를, 또는 직접 준 좌표를 저장합니다.

    좌표를 직접 주는 길이 필요한 이유 - 시작 위치(원점 0,0,0)를 HOME 으로
    두고 싶은데, 로봇이 이미 다른 데 가 있으면 저장하러 되돌아가야 합니다.
    """
    if explicit is not None:
        pose = explicit
    else:
        if not robot.wait_for_pose():
            print("오도메트리가 없습니다. base_driver 가 떴는지 확인하세요.")
            return 1
        pose = robot.pose

    poses = load()
    poses[name] = pose
    store(poses)
    print(f"{name} 저장했습니다.")
    print(describe(name, pose))
    return 0


def cmd_list():
    poses = load()
    saved = {k: v for k, v in poses.items() if k in PLACES}
    if not saved:
        print("저장된 좌표가 없습니다. python3 tools/demo.py save home 부터 하세요.")
        return 1
    print("저장된 좌표:")
    for name in PLACES:
        if name in saved:
            print(describe(name, saved[name]))
    missing = [n for n in PLACES if n not in saved]
    if missing:
        print(f"\n아직 저장 안 한 자리: {', '.join(missing)}")
    return 0


def cmd_go(robot, name):
    if name not in PLACES:
        print(f"모르는 자리입니다: {name} (가능: {', '.join(PLACES)})")
        return 2
    poses = load()
    if name not in poses:
        print(f"{name} 좌표가 없습니다. 먼저 그 자리에서:")
        print(f"  python3 tools/demo.py save {name}")
        return 1

    if not robot.wait_until_free():
        print("앞 임무가 안 끝납니다. 로그를 보세요: tail -30 /tmp/robot.log")
        return 1

    request_id = next_id("go")
    body = {
        "request_id": request_id,
        "destination": PLACES[name],
        "pose": poses[name],
        "commandName": "navigate",
    }
    print(describe(name, poses[name]))
    if name == "station":
        print("  도착하면 정밀 도킹까지 이어집니다.")
    ok = robot.send(robot._nav_pub, body, request_id, f"{name} 이동", 180.0)
    return 0 if ok else 1


def cmd_welcome(robot):
    poses = load()
    for needed in ("greeting", "home"):
        if needed not in poses:
            print(f"{needed} 좌표가 없습니다. 먼저 그 자리에서:")
            print(f"  python3 tools/demo.py save {needed}")
            return 1

    if not robot.wait_until_free():
        print("앞 임무가 안 끝납니다. 로그를 보세요: tail -30 /tmp/robot.log")
        return 1
    if robot.state != "IDLE":
        # 스테이션에 대어 놓은 동안에는 마중을 거부합니다. 급수 중에
        # 로봇이 떠나면 물이 바닥에 쏟아지기 때문입니다.
        print(f"마중은 IDLE 에서만 됩니다 (지금 {robot.state}).")
        print("먼저 HOME 으로 보내세요:  python3 tools/demo.py go home")
        return 1

    request_id = next_id("w")
    body = {
        "event_id": next_id("e"),
        "visit_id": next_id("v"),
        "request_id": request_id,
        "greeting": poses["greeting"],
        "home": poses["home"],
        "wait_seconds": 30,
        "total_timeout_seconds": 150,
        "commandName": "welcome_start",
    }
    print(describe("greeting", poses["greeting"]))
    print("  흐름: greeting 이동 -> 30초 사람 대기 -> 인사 -> home 복귀")
    print("  회신은 이 전체가 끝나야 옵니다 (최대 150초).")
    print("  로봇이 마중 자리에 서면 그 앞 1m 안으로 들어가세요.")
    ok = robot.send(robot._arrival_pub, body, request_id, "마중", 200.0)
    return 0 if ok else 1


def main():
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
        return 0

    action = args[0]
    if action == "list":
        return cmd_list()

    if action not in ("where", "save", "go", "welcome"):
        print(f"모르는 명령입니다: {action}")
        print(__doc__)
        return 2

    # 자리 이름부터 확인합니다. ROS 에 붙는 것은 그다음입니다 - 오타
    # 하나 때문에 연결을 기다릴 이유가 없습니다.
    if action in ("save", "go"):
        if len(args) < 2:
            print(f"어느 자리인지 알려주세요: {action} " + " / ".join(PLACES))
            return 2
        if args[1] not in PLACES:
            print(f"모르는 자리입니다: {args[1]} (가능: {', '.join(PLACES)})")
            return 2

    explicit = None
    if action == "save" and len(args) > 2:
        if len(args) != 5:
            print("좌표를 직접 줄 때는 셋 다 필요합니다:  save home 0 0 0")
            return 2
        try:
            explicit = {
                "x": round(float(args[2]), 3),
                "y": round(float(args[3]), 3),
                "yaw": round(float(args[4]), 3),
            }
        except ValueError:
            print("좌표는 숫자여야 합니다:  save home 0 0 0")
            return 2
        if abs(explicit["yaw"]) > 3.1416:
            print("yaw 는 -3.1416 ~ 3.1416 (라디안) 이어야 합니다.")
            return 2
        # 좌표만 넣는 것이라 ROS 에 붙을 이유가 없습니다.
        return cmd_save(None, args[1], explicit)

    robot = Robot()
    try:
        if action == "where":
            return cmd_where(robot)
        if action == "save":
            return cmd_save(robot, args[1], explicit)
        if action == "go":
            return cmd_go(robot, args[1])
        return cmd_welcome(robot)
    finally:
        robot.close()


if __name__ == "__main__":
    sys.exit(main())
