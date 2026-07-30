"""face_display — 로봇의 얼굴을 화면에 그립니다.

서버가 식물의 상태를 보고 표정을 정해 내려보내고, 로봇은 그것만 그립니다.
표정을 판단하지 않습니다.

    서버 -> MQTT command/expression -> mqtt_bridge -> display/expression -> 여기

도형 계산은 potner_base.face 에 있습니다. 이 파일은 그리기와 창 관리만
합니다. 웃는 입과 우는 입을 뒤바꾸는 실수는 화면을 봐야 알 수 있어서,
계산을 CI 가 검증할 수 있는 곳으로 빼두었습니다.

화면이 없으면(SSH 접속 등) 조용히 물러납니다. 얼굴을 못 그리는 것 때문에
로봇 전체가 죽으면 안 됩니다.
"""

import os

import cv2
import numpy as np
import rclpy
from rclpy.node import Node
from std_msgs.msg import String

from potner_base.face import (
    DEFAULT_EXPRESSION,
    face_for,
    mouth_points,
    to_pixels,
)

WINDOW = "potner_face"

# BGR. 어두운 배경에 밝은 얼굴이라 밤에 눈이 부시지 않습니다.
BACKGROUND = (24, 20, 18)
FACE_COLOR = (120, 230, 140)
REASON_COLOR = (90, 90, 90)


class FaceDisplay(Node):
    def __init__(self):
        super().__init__("face_display")

        self.declare_parameter("width", 800)
        self.declare_parameter("height", 480)
        self.declare_parameter("fullscreen", False)
        # 브링업 중에는 어떤 사유로 이 표정이 왔는지 보이는 편이 낫습니다.
        self.declare_parameter("show_reason", True)
        # 화면이 없을 때 그린 얼굴을 파일로 남깁니다. 젯슨은 DisplayPort 만
        # 내고 패시브 어댑터로는 모니터가 안 붙는데, 그걸 기다리는 동안에도
        # MQTT -> 표정 경로는 확인할 수 있어야 합니다.
        self.declare_parameter("save_path", "")

        self._width = self.get_parameter("width").value
        self._height = self.get_parameter("height").value
        self._show_reason = self.get_parameter("show_reason").value
        self._save_path = self.get_parameter("save_path").value

        self._expression = DEFAULT_EXPRESSION
        self._reason = None
        self._dirty = True

        self.create_subscription(String, "display/expression", self._on_expression, 10)
        self.create_subscription(String, "display/reason", self._on_reason, 10)

        self._window_ready = self._open_window()

    # --- 창 ---

    def _open_window(self) -> bool:
        if not os.environ.get("DISPLAY"):
            message = "DISPLAY 가 없어 창을 열지 않습니다."
            if self._save_path:
                self.get_logger().info(f"{message} 파일로만 남깁니다.")
            else:
                self.get_logger().warn(
                    f"{message} 모니터가 붙은 세션에서 실행하거나, "
                    f"save_path 를 주면 파일로 확인할 수 있습니다."
                )
            return False

        try:
            cv2.namedWindow(WINDOW, cv2.WINDOW_NORMAL)
            if self.get_parameter("fullscreen").value:
                cv2.setWindowProperty(
                    WINDOW, cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN
                )
            else:
                cv2.resizeWindow(WINDOW, self._width, self._height)
        except cv2.error as exc:
            # 젯슨에서 X 권한이 없거나 xcb 플러그인이 없을 때 여기로 옵니다.
            self.get_logger().error(f"창을 열지 못했습니다: {exc}")
            return False

        self.get_logger().info(f"얼굴 표시 시작 ({self._width}x{self._height})")
        return True

    # --- ROS ---

    def _on_expression(self, msg: String):
        # 서버가 30초마다 같은 값을 다시 보냅니다. 바뀔 때만 다시 그립니다.
        if msg.data == self._expression:
            return
        self.get_logger().info(f"표정 변경: {self._expression} -> {msg.data}")
        self._expression = msg.data
        self._dirty = True

    def _on_reason(self, msg: String):
        reason = msg.data or None
        if reason == self._reason:
            return
        self._reason = reason
        self._dirty = True

    # --- 그리기 ---

    def render(self) -> None:
        if not self._window_ready and not self._save_path:
            return

        canvas = np.full((self._height, self._width, 3), BACKGROUND, dtype=np.uint8)
        face = face_for(self._expression)

        for eye in (face.left_eye, face.right_eye):
            self._draw_eye(canvas, eye)
        self._draw_mouth(canvas, face)

        if self._show_reason and self._reason:
            cv2.putText(
                canvas, self._reason, (16, self._height - 16),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, REASON_COLOR, 1, cv2.LINE_AA,
            )

        if self._window_ready:
            cv2.imshow(WINDOW, canvas)

        if self._save_path:
            if cv2.imwrite(self._save_path, canvas):
                self.get_logger().info(f"{self._expression} -> {self._save_path}")
            else:
                # 경로가 없거나 권한이 없을 때입니다. 확장자를 빼먹어도 실패합니다.
                self.get_logger().error(f"저장 실패: {self._save_path}")
                self._save_path = ""  # 매 프레임 같은 에러를 쏟지 않게

    def _draw_eye(self, canvas, eye) -> None:
        center = to_pixels((eye.center_x, eye.center_y), self._width, self._height)
        radius = int(round(eye.radius * min(self._width, self._height)))

        # openness 가 낮으면 세로로 눌린 타원이 됩니다. 0 에 가까우면 선이
        # 되는데, 두께가 0 이면 아무것도 안 그려지므로 최소 1 을 보장합니다.
        height = max(1, int(round(radius * eye.openness)))
        cv2.ellipse(canvas, center, (radius, height), 0, 0, 360, FACE_COLOR, -1)

    def _draw_mouth(self, canvas, face) -> None:
        left, middle, right = (
            to_pixels(point, self._width, self._height)
            for point in mouth_points(face)
        )

        thickness = max(2, int(round(0.012 * min(self._width, self._height))))

        # 세 점을 지나는 곡선. 이차 베지에를 점으로 찍어 폴리라인으로 그립니다.
        # cv2 에 곡선 함수가 없어서 직접 계산합니다. 제어점은 가운데 점을
        # 두 배로 당겨야 곡선이 그 점을 실제로 지납니다.
        control = (2 * middle[0] - (left[0] + right[0]) / 2,
                   2 * middle[1] - (left[1] + right[1]) / 2)

        points = []
        for step in range(21):
            t = step / 20.0
            inv = 1.0 - t
            x = inv * inv * left[0] + 2 * inv * t * control[0] + t * t * right[0]
            y = inv * inv * left[1] + 2 * inv * t * control[1] + t * t * right[1]
            points.append((int(round(x)), int(round(y))))

        cv2.polylines(
            canvas, [np.array(points, dtype=np.int32)], False,
            FACE_COLOR, thickness, cv2.LINE_AA,
        )


def main(args=None):
    rclpy.init(args=args)
    node = FaceDisplay()

    try:
        while rclpy.ok():
            # cv2 의 창 처리는 메인 스레드에서 해야 합니다. 그래서 spin() 대신
            # spin_once 와 waitKey 를 번갈아 돌립니다.
            rclpy.spin_once(node, timeout_sec=0.05)

            if node._dirty:
                node.render()
                node._dirty = False

            if node._window_ready and cv2.waitKey(1) == 27:  # ESC
                break
    except KeyboardInterrupt:
        pass
    finally:
        cv2.destroyAllWindows()
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
