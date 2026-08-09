#!/usr/bin/env python3
"""축간거리 실측 도구 - 로봇을 정확히 N바퀴 돌리고 오차를 계산합니다.

로봇이 좌표를 제대로 못 찾아갈 때 씁니다. 원인은 대개 오도메트리가
회전을 실제와 다르게 세는 것이고, 그건 wheel_separation 이 실제와
다르기 때문입니다 (potner_base/wheel_calibration.py 머리말 참고).

사용법 - 두 단계입니다.

    # 1단계: 로봇을 3바퀴 돌립니다 (약 40초)
    python3 tools/calibrate_turn.py

    # 2단계-A: 눈대중 각도로 (쉬움)
    python3 tools/calibrate_turn.py --deviation 15

    # 2단계-B: 자로 잰 값으로 (정확함)
    python3 tools/calibrate_turn.py --front 0.09 --rear 0.02 --length 0.30

준비물: 바닥에 붙일 테이프 한 줄, 자.

★ 안전 정지를 켠 채로도 동작하지만, 라이다에 뭐가 잡혀 있으면 로봇이
  돌지 않습니다. use_safety:=false 로 띄운 상태를 권합니다.
"""

import argparse
import math
import sys

TURN_SPEED = 0.5  # rad/s
SLOW_ANGLE = math.radians(60.0)  # 이 각도 남으면 감속 시작
MIN_SPEED = 0.15  # rad/s. 감속 바닥
STOP_MARGIN = math.radians(2.0)  # 정지 지연을 감안해 일찍 멈춥니다
CONTROL_PERIOD = 0.05  # 20Hz


def compute(deviation, configured, turns):
    """틀어진 각도로 새 축간거리를 계산해 출력합니다."""
    from potner_base.wheel_calibration import corrected_separation, plausible

    odometry = 360.0 * turns
    actual = odometry + deviation
    corrected = corrected_separation(configured, odometry, actual)
    ok, note = plausible(configured, corrected)

    print()
    print("=" * 58)
    print(f"  오도메트리가 센 회전량 : {odometry:.1f}도")
    print(f"  실제로 돈 회전량       : {actual:.1f}도 ({deviation:+.1f}도 차이)")
    print("-" * 58)
    print(f"  지금 설정된 축간거리   : {configured:.4f} m")
    print(f"  ★ 새 축간거리          : {corrected:.4f} m")
    print("-" * 58)
    print(f"  {note}")
    print("=" * 58)
    if ok:
        print()
        print("이 값을 알려주시면 설정 세 곳에 반영해 드립니다.")
    return 0 if ok else 1


def run_turns(turns):
    """로봇을 오도메트리 기준으로 정확히 turns 바퀴 돌립니다."""
    import rclpy
    from geometry_msgs.msg import Twist
    from nav_msgs.msg import Odometry
    from rclpy.node import Node

    target = 2.0 * math.pi * turns

    class Turner(Node):
        def __init__(self):
            super().__init__("calibrate_turn")
            # teleop 슬롯(우선순위 200)으로 보냅니다. 자율주행(100)이나
            # 도킹(150)이 끼어들어도 이쪽이 이깁니다.
            self._pub = self.create_publisher(Twist, "cmd_vel_teleop", 10)
            self.create_subscription(Odometry, "odom", self._on_odom, 10)
            self.create_timer(CONTROL_PERIOD, self._tick)
            self._last_yaw = None
            self._turned = 0.0
            self._done = False
            self._reported = -1

        def _on_odom(self, msg):
            q = msg.pose.pose.orientation
            yaw = math.atan2(
                2.0 * (q.w * q.z + q.x * q.y),
                1.0 - 2.0 * (q.y * q.y + q.z * q.z),
            )
            if self._last_yaw is not None:
                # 연속한 두 표본의 최단각 차를 더합니다. 한 번에 빼면
                # 180도를 넘는 회전을 잴 수 없습니다.
                delta = math.atan2(
                    math.sin(yaw - self._last_yaw),
                    math.cos(yaw - self._last_yaw),
                )
                self._turned += abs(delta)
            self._last_yaw = yaw

        def _tick(self):
            if self._done:
                return
            if self._last_yaw is None:
                return  # 오도메트리를 아직 못 받았습니다

            remaining = target - self._turned
            if remaining <= STOP_MARGIN:
                self._pub.publish(Twist())
                self._done = True
                return

            speed = TURN_SPEED
            if remaining < SLOW_ANGLE:
                speed = max(MIN_SPEED, TURN_SPEED * remaining / SLOW_ANGLE)

            twist = Twist()
            twist.angular.z = speed
            self._pub.publish(twist)

            done_deg = int(math.degrees(self._turned))
            if done_deg // 30 != self._reported:
                self._reported = done_deg // 30
                print(f"  ... {done_deg}도 / {int(math.degrees(target))}도")

    rclpy.init()
    node = Turner()
    print(f"로봇을 {turns}바퀴({int(math.degrees(target))}도) 돌립니다.")
    print("멈출 때까지 기다리세요. 손대지 마세요.\n")
    try:
        while rclpy.ok() and not node._done:
            rclpy.spin_once(node, timeout_sec=0.1)
    except KeyboardInterrupt:
        pass
    finally:
        node._pub.publish(Twist())  # 어떤 경우에도 멈춥니다
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()

    print("\n회전이 끝났습니다.\n")
    print("3바퀴를 돌았으니 로봇은 처음 방향으로 돌아와 있어야 합니다.")
    print("시작 전에 붙여둔 테이프 선과 로봇 옆면을 비교하세요.\n")
    print("[쉬운 방법] 눈대중으로 몇 도 틀어졌는지만 보고:")
    print("  python3 tools/calibrate_turn.py --deviation 15")
    print("  (왼쪽으로 더 돌았으면 양수, 덜 돌았으면 음수)\n")
    print("[정확한 방법] 옆면과 테이프 사이 간격을 앞뒤로 재서:")
    print("  python3 tools/calibrate_turn.py --front 0.09 --rear 0.02 --length 0.30")
    print("  (앞끝 간격 / 뒤끝 간격 / 로봇 길이, 단위는 m)")
    return 0


def main():
    parser = argparse.ArgumentParser(description="축간거리 실측 보정")
    parser.add_argument("--turns", type=int, default=3, help="돌릴 바퀴 수")
    parser.add_argument(
        "--deviation", type=float, help="눈대중으로 본 틀어진 각도 (deg)"
    )
    parser.add_argument("--front", type=float, help="앞끝이 기준선에서 떨어진 거리 (m)")
    parser.add_argument("--rear", type=float, help="뒤끝이 기준선에서 떨어진 거리 (m)")
    parser.add_argument("--length", type=float, help="로봇 앞끝~뒤끝 길이 (m)")
    parser.add_argument(
        "--configured", type=float, default=0.230, help="지금 설정된 축간거리 (m)"
    )
    args = parser.parse_args()

    if args.deviation is not None:
        return compute(args.deviation, args.configured, args.turns)

    measured = (args.front, args.rear, args.length)
    if all(value is not None for value in measured):
        from potner_base.wheel_calibration import deviation_from_offsets

        return compute(
            deviation_from_offsets(args.front, args.rear, args.length),
            args.configured,
            args.turns,
        )
    if any(value is not None for value in measured):
        print("--front, --rear, --length 는 셋 다 필요합니다.", file=sys.stderr)
        return 2
    return run_turns(args.turns)


if __name__ == "__main__":
    sys.exit(main())
