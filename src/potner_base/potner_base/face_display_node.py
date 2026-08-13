"""face_display — 로봇의 얼굴을 화면에 그립니다.

서버가 식물의 상태를 보고 표정을 정해 내려보내고, 로봇은 그것만 그립니다.
표정을 판단하지 않습니다.

    서버 -> MQTT command/expression -> mqtt_bridge -> display/expression -> 여기

도형 계산은 potner_base.face 에 있습니다. 이 파일은 그리기와 창 관리만
합니다. 웃는 입과 우는 입을 뒤바꾸는 실수는 화면을 봐야 알 수 있어서,
계산을 CI 가 검증할 수 있는 곳으로 빼두었습니다.

화면이 없으면(SSH 접속, 로그인 전 등) 조용히 물러납니다. 얼굴을 못 그리는
것 때문에 주행과 안전 정지가 막히면 안 됩니다.
"""

import glob
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
    parse_drm_modes,
    scale,
    to_pixels,
    viewport_for,
)

WINDOW = "potner_face"

# BGR. 어두운 배경에 밝은 얼굴이라 밤에 눈이 부시지 않습니다.
BACKGROUND = (24, 20, 18)
FACE_COLOR = (120, 230, 140)
REASON_COLOR = (90, 90, 90)

# 해상도를 못 알아냈을 때 씁니다. 7인치 LCD 기준입니다.
FALLBACK_SIZE = (1024, 600)


class FaceDisplay(Node):
    def __init__(self):
        super().__init__("face_display")

        # 0 이면 연결된 화면의 해상도를 커널에서 읽어 씁니다. 7인치 LCD 와
        # 시험용 모니터의 해상도가 달라도 설정을 안 고치게 하려는 것입니다.
        self.declare_parameter("width", 0)
        self.declare_parameter("height", 0)
        self.declare_parameter("fullscreen", True)
        # 브링업 중에는 어떤 사유로 이 표정이 왔는지 보이는 편이 낫습니다.
        self.declare_parameter("show_reason", True)
        # 화면이 없을 때 그린 얼굴을 파일로 남깁니다.
        self.declare_parameter("save_path", "")

        self._show_reason = self.get_parameter("show_reason").value
        self._save_path = self.get_parameter("save_path").value
        self._width, self._height = self._resolve_size()
        self._view = viewport_for(self._width, self._height)

        self._expression = DEFAULT_EXPRESSION
        self._reason = None
        self._dirty = True
        # 그린 결과를 들고 있습니다. 창에 올리는 일(show)과 그리는 일(render)을
        # 나눈 이유는 아래 show() 주석에 있습니다.
        self._canvas = None
        self._fullscreen_pending = False

        self.create_subscription(String, "display/expression", self._on_expression, 10)
        self.create_subscription(String, "display/reason", self._on_reason, 10)

        self._window_ready = self._open_window()

    # --- 화면 크기 ---

    def _resolve_size(self):
        width = self.get_parameter("width").value
        height = self.get_parameter("height").value
        if width > 0 and height > 0:
            return width, height

        detected = self._detect_size()
        if detected:
            self.get_logger().info(f"화면 해상도 감지: {detected[0]}x{detected[1]}")
            return detected

        self.get_logger().warn(
            f"화면 해상도를 못 읽어 {FALLBACK_SIZE[0]}x{FALLBACK_SIZE[1]} 로 "
            f"그립니다. 다르면 width/height 를 직접 주세요."
        )
        return FALLBACK_SIZE

    def _detect_size(self):
        """연결된 DRM 커넥터의 선호 해상도를 읽습니다.

        xrandr 이 없어도 되고 X 세션 밖에서도 읽힙니다. 젯슨은 DisplayPort
        하나만 내므로 보통 커넥터가 하나입니다.
        """
        for status_path in sorted(glob.glob("/sys/class/drm/*/status")):
            try:
                with open(status_path, encoding="utf-8") as handle:
                    if handle.read().strip() != "connected":
                        continue
                modes_path = os.path.join(os.path.dirname(status_path), "modes")
                with open(modes_path, encoding="utf-8") as handle:
                    size = parse_drm_modes(handle.read())
            except OSError:
                continue
            if size:
                return size
        return None

    # --- 창 ---

    def _open_window(self):
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
                # ★ 전체화면은 여기서 걸지 않고 첫 프레임을 올린 뒤에 겁니다.
                #   GTK 백엔드는 첫 imshow 전까지 창을 실제로 만들지 않아서,
                #   지금 설정하면 조용히 무시됩니다. 로그에는 "시작"이 찍히고
                #   화면에는 아무것도 안 뜨는 상태가 됩니다.
                self._fullscreen_pending = True
            else:
                cv2.resizeWindow(WINDOW, self._width, self._height)
        except cv2.error as exc:
            # X 권한이 없거나 GTK 백엔드를 못 띄울 때 여기로 옵니다.
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

    def render(self):
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

        self._canvas = canvas

        if self._save_path:
            if cv2.imwrite(self._save_path, canvas):
                self.get_logger().info(f"{self._expression} -> {self._save_path}")
            else:
                # 경로가 없거나 권한이 없을 때입니다. 확장자를 빼먹어도 실패합니다.
                self.get_logger().error(f"저장 실패: {self._save_path}")
                self._save_path = ""  # 매 프레임 같은 에러를 쏟지 않게

    def show(self):
        """그려둔 얼굴을 창에 올립니다. **매 주기 호출합니다.**

        표정이 바뀔 때만 imshow 하면, 창이 가려졌다 드러날 때(expose) 내용을
        다시 그려줄 사람이 없어서 빈 화면이 됩니다. 캔버스는 이미 만들어져
        있으니 올리는 비용은 무시할 만합니다.
        """
        if not self._window_ready or self._canvas is None:
            return

        cv2.imshow(WINDOW, self._canvas)

        if self._fullscreen_pending:
            # 첫 프레임을 올린 뒤라 이제 창이 실제로 존재합니다.
            try:
                cv2.setWindowProperty(
                    WINDOW, cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN
                )
            except cv2.error as exc:
                self.get_logger().warn(f"전체화면 설정 실패: {exc}")
            self._fullscreen_pending = False

    def _draw_eye(self, canvas, eye):
        center = to_pixels((eye.center_x, eye.center_y), self._view)
        radius = scale(eye.radius, self._view)

        # openness 가 낮으면 세로로 눌린 타원이 됩니다. 0 에 가까우면 선이
        # 되는데, 두께가 0 이면 아무것도 안 그려지므로 최소 1 을 보장합니다.
        height = max(1, int(round(radius * eye.openness)))
        cv2.ellipse(canvas, center, (radius, height), 0, 0, 360, FACE_COLOR, -1)

    def _draw_mouth(self, canvas, face):
        left, middle, right = (
            to_pixels(point, self._view) for point in mouth_points(face)
        )

        thickness = max(2, scale(0.012, self._view))

        # 세 점을 지나는 곡선. cv2 에 곡선 함수가 없어서 이차 베지에를 점으로
        # 찍어 폴리라인으로 그립니다. 제어점은 가운데 점을 두 배로 당겨야
        # 곡선이 그 점을 실제로 지납니다.
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

            node.show()

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
