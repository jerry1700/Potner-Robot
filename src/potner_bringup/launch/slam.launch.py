"""집 지도를 만듭니다 (SLAM).

    터미널 1:  ros2 launch potner_bringup robot.launch.py
    터미널 2:  ros2 launch potner_bringup slam.launch.py
    터미널 3:  ros2 run teleop_twist_keyboard teleop_twist_keyboard \\
                   --ros-args -r cmd_vel:=cmd_vel_teleop

키보드로 로봇을 천천히 몰고 집을 한 바퀴 돌면 지도가 그려집니다.
빠르게 몰면 스캔 정합이 실패해서 지도가 겹쳐 그려집니다. 천천히요.

다 돌았으면 지도를 저장하세요:
    ros2 run nav2_map_server map_saver_cli -f ~/potner_ws/maps/home
"""

import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch_ros.actions import Node


def generate_launch_description():
    share = get_package_share_directory("potner_bringup")
    slam_params = os.path.join(share, "config", "slam_toolbox.yaml")

    return LaunchDescription([
        Node(
            package="slam_toolbox",
            executable="async_slam_toolbox_node",
            name="slam_toolbox",
            parameters=[slam_params],
            output="screen",
        ),
        Node(
            package="rviz2",
            executable="rviz2",
            arguments=[
                "-d",
                os.path.join(
                    get_package_share_directory("potner_description"),
                    "rviz",
                    "potner.rviz",
                ),
            ],
        ),
    ])
