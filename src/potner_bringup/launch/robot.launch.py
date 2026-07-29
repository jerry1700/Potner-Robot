"""로봇 본체를 통째로 띄웁니다.

    ros2 launch potner_bringup robot.launch.py

여기에는 자율주행(Nav2)이 포함되지 않습니다. 먼저 이 launch 로 센서와
모터가 살아있는지 확인한 뒤, slam.launch.py 로 지도를 만들고,
nav2.launch.py 로 자율주행을 얹는 순서로 진행하세요.

한 번에 다 켜면 무엇이 고장났는지 알 수 없습니다.
"""

import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.conditions import IfCondition
from launch.substitutions import Command, LaunchConfiguration
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterValue


def generate_launch_description():
    bringup_share = get_package_share_directory("potner_bringup")
    description_share = get_package_share_directory("potner_description")

    params = os.path.join(bringup_share, "config", "potner_params.yaml")
    twist_mux_params = os.path.join(bringup_share, "config", "twist_mux.yaml")
    ydlidar_params = os.path.join(bringup_share, "config", "ydlidar.yaml")
    xacro_path = os.path.join(description_share, "urdf", "potner.urdf.xacro")

    use_camera = LaunchConfiguration("use_camera")
    use_lidar = LaunchConfiguration("use_lidar")

    robot_description = ParameterValue(Command(["xacro ", xacro_path]), value_type=str)

    return LaunchDescription([
        DeclareLaunchArgument("use_camera", default_value="true"),
        DeclareLaunchArgument("use_lidar", default_value="true"),

        # ---- 형상: URDF로부터 고정 TF를 발행합니다. Nav2의 전제조건.
        Node(
            package="robot_state_publisher",
            executable="robot_state_publisher",
            parameters=[{"robot_description": robot_description}],
        ),

        # ---- 하드웨어: ESP32와 통신하며 odom -> base_link TF를 발행
        Node(
            package="potner_base",
            executable="base_driver",
            parameters=[params],
            output="screen",
        ),

        # ---- 주행 명령 중재: 여러 노드의 cmd_vel 을 하나로 정리
        Node(
            package="twist_mux",
            executable="twist_mux",
            parameters=[twist_mux_params],
            remappings=[("cmd_vel_out", "cmd_vel")],
            output="screen",
        ),

        # ---- 안전: 최우선 정지. 다른 무엇보다 먼저 떠 있어야 합니다.
        Node(
            package="potner_mission",
            executable="safety",
            parameters=[params],
            output="screen",
        ),

        # ---- 식물·배터리 센서: mission_manager 의 판단 입력
        Node(
            package="potner_base",
            executable="plant_sensors",
            parameters=[params],
            output="screen",
        ),

        # ---- 스피커: tts/say 를 소리로 내보냅니다
        Node(
            package="potner_base",
            executable="speaker",
            parameters=[params],
        ),

        # ---- 센서
        Node(
            package="v4l2_camera",
            executable="v4l2_camera_node",
            condition=IfCondition(use_camera),
            parameters=[{
                "video_device": "/dev/video0",
                "image_size": [640, 480],
            }],
        ),
        # YDLIDAR 드라이버는 apt에 없어 소스 빌드가 필요합니다. 설치 방법은
        # README 를 보세요. 반드시 humble 브랜치를 받아야 합니다.
        #
        # 드라이버가 제공하는 ydlidar_launch.py 대신 노드만 직접 띄웁니다.
        # 그 launch 는 base_link -> laser_frame 을 2cm 로 발행해서 우리
        # URDF(48.8cm)와 충돌합니다. 로봇 형상은 URDF 가 소유합니다.
        Node(
            package="ydlidar_ros2_driver",
            executable="ydlidar_ros2_driver_node",
            name="ydlidar_ros2_driver_node",
            condition=IfCondition(use_lidar),
            parameters=[ydlidar_params],
            output="screen",
        ),

        # ---- 인지
        Node(
            package="potner_perception",
            executable="marker_detector",
            parameters=[params],
        ),
        Node(
            package="potner_perception",
            executable="person_detector",
            parameters=[params],
        ),

        # ---- 임무
        Node(
            package="potner_docking",
            executable="docking_server",
            parameters=[params],
            output="screen",
        ),
        Node(
            package="potner_mission",
            executable="mission_manager",
            parameters=[params],
            output="screen",
        ),

        # ---- 외부 연동
        Node(
            package="potner_bridge",
            executable="mqtt_bridge",
            parameters=[params],
        ),
    ])
