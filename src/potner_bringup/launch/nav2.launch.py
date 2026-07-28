"""저장된 지도 위에서 자율주행합니다 (Nav2 + AMCL).

    터미널 1:  ros2 launch potner_bringup robot.launch.py
    터미널 2:  ros2 launch potner_bringup nav2.launch.py map:=/home/e104/potner_ws/maps/home.yaml

RViz에서 "2D Pose Estimate"로 로봇의 현재 위치를 한 번 찍어준 뒤,
"2D Goal Pose"로 목적지를 지정하면 알아서 갑니다.

주의: Nav2의 cmd_vel 출력을 cmd_vel_nav 로 돌려놓았습니다. twist_mux 를
거치지 않고 모터로 직행하면 안전 정지가 무력화됩니다.
"""

import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration


def generate_launch_description():
    bringup_share = get_package_share_directory("potner_bringup")
    nav2_share = get_package_share_directory("nav2_bringup")

    nav2_params = os.path.join(bringup_share, "config", "nav2_params.yaml")

    return LaunchDescription([
        DeclareLaunchArgument(
            "map",
            description="map_saver_cli 로 저장한 지도 yaml 경로",
        ),
        DeclareLaunchArgument("use_sim_time", default_value="false"),

        IncludeLaunchDescription(
            PythonLaunchDescriptionSource(
                os.path.join(nav2_share, "launch", "bringup_launch.py")
            ),
            launch_arguments={
                "map": LaunchConfiguration("map"),
                "use_sim_time": LaunchConfiguration("use_sim_time"),
                "params_file": nav2_params,
            }.items(),
        ),
    ])
