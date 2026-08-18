"""base_driver — 젯슨과 ESP32를 잇는 유일한 노드.

이 노드가 하는 일은 딱 두 가지입니다.
    1. /cmd_vel 을 받아 좌우 바퀴 속도로 바꿔 ESP32에 내려보낸다
    2. ESP32가 올려보낸 엔코더 카운트로 /odom 과 TF(odom->base_link)를 만든다

모터 PWM 생성과 바퀴 속도 PID는 전부 ESP32 펌웨어가 합니다. 젯슨은
실시간 OS가 아니라서 여기서 PID를 돌리면 주기가 흔들립니다.
"""

import math

import rclpy
from geometry_msgs.msg import Twist, TransformStamped
from nav_msgs.msg import Odometry
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import JointState, Range
from tf2_ros import TransformBroadcaster

from potner_base import serial_protocol as proto
from potner_base.kinematics import DriveConfig, OdometryIntegrator, twist_to_wheel_speeds

try:
    import serial  # pyserial
except ImportError:  # 하드웨어 없는 개발 PC에서도 노드 파일을 열어볼 수 있게
    serial = None


class BaseDriver(Node):
    def __init__(self):
        super().__init__("base_driver")

        self.declare_parameter("serial_port", "/dev/ttyUSB_ESP32")
        self.declare_parameter("baud_rate", 115200)
        self.declare_parameter("wheel_diameter", 0.060)
        self.declare_parameter("wheel_separation", 0.2179)
        self.declare_parameter("counts_per_rev", 5760)
        self.declare_parameter("max_wheel_speed", 0.25)
        self.declare_parameter("cmd_vel_timeout", 0.5)
        self.declare_parameter("publish_rate", 30.0)

        self.cfg = DriveConfig(
            wheel_diameter=self.get_parameter("wheel_diameter").value,
            wheel_separation=self.get_parameter("wheel_separation").value,
            counts_per_rev=self.get_parameter("counts_per_rev").value,
            max_wheel_speed=self.get_parameter("max_wheel_speed").value,
        )
        self.odom = OdometryIntegrator(self.cfg)
        self.cmd_vel_timeout = self.get_parameter("cmd_vel_timeout").value

        self._target = (0.0, 0.0)
        self._last_cmd_time = self.get_clock().now()
        self._last_feedback_time = None
        self._wheel_angle = [0.0, 0.0]

        self._serial = self._open_serial()

        self.create_subscription(Twist, "cmd_vel", self._on_cmd_vel, 10)
        self._odom_pub = self.create_publisher(Odometry, "odom", 10)
        self._joint_pub = self.create_publisher(JointState, "joint_states", 10)
        self._bumper_pubs = {
            "left": self.create_publisher(Range, "bumper/left", qos_profile_sensor_data),
            "right": self.create_publisher(Range, "bumper/right", qos_profile_sensor_data),
        }
        self._tf = TransformBroadcaster(self)

        period = 1.0 / self.get_parameter("publish_rate").value
        self.create_timer(period, self._spin_once)

        self.get_logger().info(f"base_driver 시작. 포트={self._port_name}")

    # --- 시리얼 ---

    def _open_serial(self):
        self._port_name = self.get_parameter("serial_port").value
        baud = self.get_parameter("baud_rate").value

        if serial is None:
            self.get_logger().warn("pyserial 미설치 — 시뮬레이션 모드로 동작합니다.")
            return None

        try:
            return serial.Serial(self._port_name, baud, timeout=0.05)
        except Exception as exc:  # 포트 없음, 권한 없음 등
            self.get_logger().error(
                f"시리얼 포트 열기 실패 ({self._port_name}): {exc} — "
                "시뮬레이션 모드로 동작합니다."
            )
            return None

    def _write(self, frame):
        if self._serial is None:
            return
        try:
            self._serial.write(frame.encode("ascii"))
        except Exception as exc:
            self.get_logger().error(f"시리얼 쓰기 실패: {exc}")

    def _read_feedback(self):
        """수신 버퍼에 쌓인 줄 중 가장 최신 프레임 하나를 돌려줍니다."""
        if self._serial is None:
            return None

        latest = None
        try:
            while self._serial.in_waiting:
                raw = self._serial.readline().decode("ascii", errors="ignore")
                try:
                    latest = proto.decode_feedback(raw)
                except proto.ProtocolError as exc:
                    # 노이즈 한 줄로 노드를 죽이지 않습니다. throttle 로 로그 폭주 방지.
                    self.get_logger().warn(
                        f"프레임 버림: {exc}", throttle_duration_sec=2.0
                    )
        except Exception as exc:
            self.get_logger().error(f"시리얼 읽기 실패: {exc}")
        return latest

    # --- 콜백 ---

    def _on_cmd_vel(self, msg: Twist):
        self._target = twist_to_wheel_speeds(msg.linear.x, msg.angular.z, self.cfg)
        self._last_cmd_time = self.get_clock().now()

    def _spin_once(self):
        now = self.get_clock().now()

        # 워치독: 상위 노드가 조용하면 멈춥니다. Nav2가 죽어도 로봇은 서야 합니다.
        idle = (now - self._last_cmd_time).nanoseconds * 1e-9
        if idle > self.cmd_vel_timeout or max(abs(self._target[0]), abs(self._target[1])) < 1e-6:
            self._target = (0.0, 0.0)

        # 목표가 0이면 V,0,0 이 아니라 S 를 보냅니다. V,0,0 은 펌웨어 PID를
        # 계속 돌리는 명령이라, 엔코더 선이 빠져 측정이 0으로 얼어붙으면
        # 주행 중 감긴 적분항이 풀리지 않아 바퀴가 멈추지 않습니다 (실기에서
        # 당했습니다). S 는 펌웨어가 PID 상태를 통째로 리셋하므로 엔코더
        # 상태와 무관하게 무조건 멈춥니다.
        if self._target == (0.0, 0.0):
            self._write(proto.encode_stop())
        else:
            self._write(proto.encode_velocity(*self._target))

        feedback = self._read_feedback()
        if feedback is None:
            return

        dt = self._elapsed_since_last_feedback(now)
        if dt is None:
            return

        self.odom.update(feedback.left_ticks, feedback.right_ticks, dt)
        self._publish_odom(now)
        self._publish_joints(now, feedback, dt)
        self._publish_bumpers(now, feedback)

    def _elapsed_since_last_feedback(self, now):
        if self._last_feedback_time is None:
            self._last_feedback_time = now
            return None
        dt = (now - self._last_feedback_time).nanoseconds * 1e-9
        self._last_feedback_time = now
        return dt if dt > 0.0 else None

    # --- 발행 ---

    def _publish_odom(self, stamp):
        x, y, theta = self.odom.pose
        qz = math.sin(theta / 2.0)
        qw = math.cos(theta / 2.0)

        # ★ 자식 프레임은 base_link 가 아니라 base_footprint 입니다.
        #
        #   URDF 가 base_footprint -> base_link 를 이미 발행합니다
        #   (potner.urdf.xacro 의 base_joint, 고정 조인트라 /tf_static).
        #   여기서 odom -> base_link 를 내보내면 base_link 에 부모가 둘이
        #   되어 TF 트리가 두 조각으로 갈라집니다. 그러면 slam_toolbox 가
        #   "Failed to compute odom pose" 를 쏟아내며 스캔을 전부 버려서
        #   **지도가 한 장도 안 만들어집니다.** 실기에서 그렇게 당했습니다.
        #
        #   올바른 사슬은 map -> odom -> base_footprint -> base_link 이고,
        #   slam_toolbox.yaml 의 base_frame 과 nav2_params.yaml 의
        #   base_frame_id / robot_base_frame 이 전부 base_footprint 입니다.
        #
        #   오도메트리가 재는 것은 바닥에 투영된 평면 자세라 base_footprint
        #   가 의미상으로도 맞습니다 — base_link 는 거기서 바퀴 반지름만큼
        #   떠 있고, 그 차이는 URDF 가 채웁니다.
        msg = Odometry()
        msg.header.stamp = stamp.to_msg()
        msg.header.frame_id = "odom"
        msg.child_frame_id = "base_footprint"
        msg.pose.pose.position.x = x
        msg.pose.pose.position.y = y
        msg.pose.pose.orientation.z = qz
        msg.pose.pose.orientation.w = qw
        msg.twist.twist.linear.x = self.odom.linear_velocity
        msg.twist.twist.angular.z = self.odom.angular_velocity
        self._odom_pub.publish(msg)

        tf = TransformStamped()
        tf.header.stamp = stamp.to_msg()
        tf.header.frame_id = "odom"
        tf.child_frame_id = "base_footprint"
        tf.transform.translation.x = x
        tf.transform.translation.y = y
        tf.transform.rotation.z = qz
        tf.transform.rotation.w = qw
        self._tf.sendTransform(tf)

    def _publish_joints(self, stamp, feedback, dt):
        """RViz에서 바퀴가 실제로 굴러가는 걸 보여주기 위한 관절 상태."""
        for index, speed in enumerate(self._target):
            self._wheel_angle[index] += (
                speed / (self.cfg.wheel_diameter / 2.0)
            ) * dt

        msg = JointState()
        msg.header.stamp = stamp.to_msg()
        msg.name = ["left_wheel_joint", "right_wheel_joint"]
        msg.position = list(self._wheel_angle)
        self._joint_pub.publish(msg)

    def _publish_bumpers(self, stamp, feedback):
        """LiDAR가 못 보는 발밑 장애물. safety_node 가 구독합니다."""
        readings = {
            "left": feedback.left_bumper_mm,
            "right": feedback.right_bumper_mm,
        }
        for side, millimeters in readings.items():
            msg = Range()
            msg.header.stamp = stamp.to_msg()
            msg.header.frame_id = f"bumper_{side}_link"
            msg.radiation_type = Range.INFRARED
            msg.field_of_view = 0.44  # VL53L0X 약 25도
            msg.min_range = 0.03
            msg.max_range = 1.20
            msg.range = millimeters / 1000.0
            self._bumper_pubs[side].publish(msg)

    def destroy_node(self):
        self._write(proto.encode_stop())
        if self._serial is not None:
            self._serial.close()
        super().destroy_node()


def main(args=None):
    rclpy.init(args=args)
    node = BaseDriver()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
