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
    camera_device = LaunchConfiguration("camera_device")
    use_person_detector = LaunchConfiguration("use_person_detector")
    use_simple_nav = LaunchConfiguration("use_simple_nav")
    use_safety = LaunchConfiguration("use_safety")

    robot_description = ParameterValue(Command(["xacro ", xacro_path]), value_type=str)

    return LaunchDescription([
        DeclareLaunchArgument("use_camera", default_value="true"),
        DeclareLaunchArgument("use_lidar", default_value="true"),
        # udev 규칙이 만드는 고정 이름입니다 (README "USB 장치 이름 고정").
        # /dev/video0 을 그대로 쓰면 안 되는 이유가 두 가지입니다. UVC 웹캠은
        # 영상용과 메타데이터용 노드를 함께 만들어서 번호가 둘 이상 생기고,
        # 그 번호가 꽂는 순서와 부팅 타이밍에 따라 바뀝니다. 어긋나면
        # v4l2_camera 가 "No such file or directory" 로 조용히 물러나고,
        # marker_detector 는 영영 아무것도 발행하지 않아 도킹이 15초 뒤
        # "마커를 찾지 못함" 으로 끝납니다 — 원인이 카메라라는 단서가 없습니다.
        # 규칙을 아직 안 만들었으면 camera_device:=/dev/video0 으로 넘기세요.
        DeclareLaunchArgument("camera_device", default_value="/dev/video_cam"),
        # YOLO 사람 인지. 젯슨에 ultralytics 가 없고, 있어도 카메라 높이 탓에
        # 사람을 제대로 못 봅니다. 인사는 scan_presence 가 맡습니다.
        DeclareLaunchArgument("use_person_detector", default_value="false"),
        # Nav2 대신 오도메트리로 목표까지 가는 간이 주행. 좁은 시연
        # 공간에서는 Nav2 가 기본 여유(55cm)만으로도 갈 칸을 다 막아
        # 경로를 못 만듭니다. Nav2 를 띄울 때는 반드시 false 로 끄세요.
        DeclareLaunchArgument("use_simple_nav", default_value="true"),
        # ★ 안전 정지. 통제된 시연장처럼 장애물이 없다고 확신할 때만
        #   끄세요 (use_safety:=false). 끄면 라이다에 뭐가 잡혀도 로봇이
        #   서지 않습니다 — 사람 발이든 스테이션이든 그대로 밀고 갑니다.
        #
        #   라이다 자체는 꺼지지 않습니다. 마중 시연의 사람 감지
        #   (scan_presence)와 도킹 탐색의 전방 확인이 라이다를 씁니다.
        DeclareLaunchArgument("use_safety", default_value="true"),

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
        # 끄면 cmd_vel_safety 에 발행자가 없어져 twist_mux 의 255 슬롯이
        # 비고, 그 아래(도킹 150 / 주행 100)가 그대로 모터까지 갑니다.
        Node(
            package="potner_mission",
            executable="safety",
            condition=IfCondition(use_safety),
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

        # ---- 얼굴: 서버가 정한 표정을 화면에 그립니다
        # DISPLAY 가 없으면(SSH 접속 등) 스스로 물러납니다. 얼굴을 못 그리는
        # 것 때문에 주행이 막히면 안 됩니다.
        Node(
            package="potner_base",
            executable="face_display",
            parameters=[params],
        ),

        # ---- 센서
        Node(
            package="v4l2_camera",
            executable="v4l2_camera_node",
            condition=IfCondition(use_camera),
            parameters=[{
                "video_device": ParameterValue(camera_device, value_type=str),
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
        # 귀가 인사 트리거. 카메라(바닥 13cm, 틸트 0)로는 사람 발밖에 안
        # 보여서 라이다(바닥 52cm)로 봅니다 — scan_presence.py 머리말 참고.
        Node(
            package="potner_perception",
            executable="scan_presence",
            parameters=[params],
            output="screen",
        ),
        # YOLO 경로. 기본으로 끕니다 — ultralytics 가 없으면 아무것도
        # 발행하지 않아 무해하지만, 누가 설치하면 scan_presence 와 함께
        # perception/person_present 를 발행하게 됩니다. 인사는 한 번 쏘면
        # 시간창이 닫히고 HOME 복귀까지 시작되므로(mission_manager
        # _on_person), 자율 발화 경로가 둘이면 오발 확률이 두 배입니다.
        Node(
            package="potner_perception",
            executable="person_detector",
            condition=IfCondition(use_person_detector),
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

        # ---- 수동 주행: 휴대폰 방향 버튼 -> twist_mux teleop 슬롯
        Node(
            package="potner_mission",
            executable="drive_node",
            parameters=[params],
            output="screen",
        ),

        # ---- 좌표 주행: Nav2 를 대신해 오도메트리로 목표까지 갑니다.
        #
        # ★ Nav2 를 쓸 때는 반드시 use_simple_nav:=false 로 끄세요. 같은
        #   액션 이름(navigate_to_pose)을 두 서버가 물면 mission_manager 가
        #   아무 쪽에나 붙어서, 어느 쪽이 로봇을 몰고 있는지 알 수 없게
        #   됩니다.
        Node(
            package="potner_mission",
            executable="simple_navigator",
            parameters=[params],
            condition=IfCondition(use_simple_nav),
            output="screen",
        ),

        # ---- 좌표 등록 도구: 지금 서 있는 좌표를 읽기 좋게 내보냅니다.
        # 로봇을 등록할 자리로 데려간 뒤 아래를 읽어 서버에 넣습니다.
        #   ros2 topic echo /robot/pose --once
        Node(
            package="potner_mission",
            executable="pose_report",
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
