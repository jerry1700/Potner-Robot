#!/usr/bin/env python3
"""Nav2와 도킹 서버를 흉내 내는 목(mock) 액션 서버.

바퀴·라이다·카메라 없이 **서버 MQTT -> mission_manager -> 서버 MQTT** 전체
왕복을 확인할 때 씁니다. ``skip_navigation`` 으로는 못 하는 것 두 가지가
여기서 됩니다.

  1. **실패 경로.** ERROR + DOCKING_FAILED / NAVIGATION_FAILED 회신이
     실제로 서버에 닿는지.
  2. **급수 스테이션 도킹.** ``skip_navigation`` 은 Nav2만 건너뛰고 도킹은
     그대로 하므로(도킹만 따로 시험하려고 그렇게 만들었습니다), 카메라가
     없으면 WATER_STATION 이동이 도킹 단계에서 멈춥니다.

이 파일은 시험 도구라 colcon 패키지에 넣지 않았습니다. ROS 2 환경만
source 하면 그대로 돌아갑니다.

    # 정상 도착
    python3 tools/mock_nav_actions.py

    # 3초 걸려 이동한 뒤 도킹 실패
    python3 tools/mock_nav_actions.py --ros-args \\
        -p duration:=3.0 -p dock_outcome:=abort

    # Nav2가 응답하지 않는 상황 (mission_manager 의 navigate_timeout 확인)
    python3 tools/mock_nav_actions.py --ros-args -p nav_outcome:=hang

★ 진짜 Nav2·도킹 서버와 **같이 띄우면 안 됩니다.** 같은 액션 이름을 두
  노드가 광고하면 클라이언트가 어느 쪽에 붙을지 알 수 없습니다.
"""

import time

import rclpy
from nav2_msgs.action import NavigateToPose
from rclpy.action import ActionServer, CancelResponse, GoalResponse
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node

from potner_msgs.action import DockToStation

OUTCOMES = ("succeed", "abort", "reject", "hang")


class MockNavActions(Node):
    def __init__(self):
        super().__init__("mock_nav_actions")

        self.declare_parameter("nav_outcome", "succeed")
        self.declare_parameter("dock_outcome", "succeed")
        self.declare_parameter("duration", 2.0)

        group = ReentrantCallbackGroup()
        self._nav_server = ActionServer(
            self,
            NavigateToPose,
            "navigate_to_pose",
            execute_callback=self._execute_nav,
            goal_callback=lambda goal: self._accept("nav_outcome"),
            cancel_callback=lambda goal: CancelResponse.ACCEPT,
            callback_group=group,
        )
        self._dock_server = ActionServer(
            self,
            DockToStation,
            "dock_to_station",
            execute_callback=self._execute_dock,
            goal_callback=lambda goal: self._accept("dock_outcome"),
            cancel_callback=lambda goal: CancelResponse.ACCEPT,
            callback_group=group,
        )

        self.get_logger().warn(
            "목 액션 서버입니다. 진짜 Nav2·도킹 서버와 함께 띄우지 마세요."
        )
        self.get_logger().info(
            f"nav_outcome={self._outcome('nav_outcome')}, "
            f"dock_outcome={self._outcome('dock_outcome')}, "
            f"duration={self._duration()}s"
        )

    # --- 설정 ---

    def _outcome(self, name):
        value = self.get_parameter(name).value
        if value not in OUTCOMES:
            self.get_logger().error(
                f"{name}={value!r} 는 모르는 값입니다. {OUTCOMES} 중 하나여야 "
                "합니다. succeed 로 봅니다."
            )
            return "succeed"
        return value

    def _duration(self):
        return max(0.0, float(self.get_parameter("duration").value))

    def _accept(self, name):
        if self._outcome(name) == "reject":
            return GoalResponse.REJECT
        return GoalResponse.ACCEPT

    def _run(self, goal_handle, outcome, tick):
        """진행하는 척하다가 결과를 정한다. 끝까지 갔으면 True."""
        if outcome == "hang":
            # 결과를 영영 돌려주지 않습니다. mission_manager 가 스스로
            # 빠져나오는지 보는 용도입니다.
            self.get_logger().warn("hang — 결과를 돌려주지 않습니다.")
            while rclpy.ok() and not goal_handle.is_cancel_requested:
                time.sleep(0.2)
            goal_handle.canceled()
            return False

        deadline = time.monotonic() + self._duration()
        while time.monotonic() < deadline:
            if goal_handle.is_cancel_requested:
                goal_handle.canceled()
                self.get_logger().info("목표가 취소됐습니다.")
                return False
            tick(max(0.0, deadline - time.monotonic()))
            time.sleep(0.2)
        return True

    # --- Nav2 ---

    def _execute_nav(self, goal_handle):
        pose = goal_handle.request.pose.pose.position
        self.get_logger().info(f"이동 목표 수신: x={pose.x:.3f}, y={pose.y:.3f}")

        def tick(remaining):
            feedback = NavigateToPose.Feedback()
            feedback.distance_remaining = float(remaining)
            goal_handle.publish_feedback(feedback)

        if not self._run(goal_handle, self._outcome("nav_outcome"), tick):
            return NavigateToPose.Result()

        if self._outcome("nav_outcome") == "abort":
            self.get_logger().warn("이동 실패로 처리합니다.")
            goal_handle.abort()
            return NavigateToPose.Result()

        self.get_logger().info("이동 성공으로 처리합니다.")
        goal_handle.succeed()
        return NavigateToPose.Result()

    # --- 도킹 ---

    def _execute_dock(self, goal_handle):
        marker_id = goal_handle.request.marker_id
        self.get_logger().info(f"도킹 목표 수신: marker_id={marker_id}")

        def tick(remaining):
            feedback = DockToStation.Feedback()
            feedback.state = "APPROACHING"
            feedback.distance = float(remaining) * 0.1
            feedback.lateral_error = 0.0
            feedback.yaw_error = 0.0
            goal_handle.publish_feedback(feedback)

        result = DockToStation.Result()
        if not self._run(goal_handle, self._outcome("dock_outcome"), tick):
            result.success = False
            result.message = "취소됨"
            return result

        if self._outcome("dock_outcome") == "abort":
            self.get_logger().warn("도킹 실패로 처리합니다.")
            result.success = False
            result.message = "마커를 찾지 못했습니다(목)."
            goal_handle.abort()
            return result

        self.get_logger().info("도킹 성공으로 처리합니다.")
        result.success = True
        result.message = "도킹 완료(목)."
        result.final_distance = 0.15
        result.final_yaw_error = 0.4
        goal_handle.succeed()
        return result


def main(args=None):
    rclpy.init(args=args)
    node = MockNavActions()
    executor = MultiThreadedExecutor()
    executor.add_node(node)
    try:
        executor.spin()
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
