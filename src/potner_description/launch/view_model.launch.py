"""URDF가 제대로 그려지는지 RViz로 확인합니다.

로봇 없이 노트북에서도 돌아갑니다. 실측값을 고칠 때마다 여기서 먼저
모양을 확인하는 게 빠릅니다.

    ros2 launch potner_description view_model.launch.py
"""

import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.substitutions import Command
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterValue


def generate_launch_description():
    share = get_package_share_directory("potner_description")
    xacro_path = os.path.join(share, "urdf", "potner.urdf.xacro")
    rviz_path = os.path.join(share, "rviz", "potner.rviz")

    robot_description = ParameterValue(
        Command(["xacro ", xacro_path]), value_type=str
    )

    return LaunchDescription([
        Node(
            package="robot_state_publisher",
            executable="robot_state_publisher",
            parameters=[{"robot_description": robot_description}],
        ),
        # 실제 로봇에서는 base_driver 가 joint_states 를 발행하므로 이 노드를
        # 띄우지 않습니다. 형상 확인용으로만 씁니다.
        Node(
            package="joint_state_publisher_gui",
            executable="joint_state_publisher_gui",
        ),
        Node(
            package="rviz2",
            executable="rviz2",
            arguments=["-d", rviz_path] if os.path.exists(rviz_path) else [],
        ),
    ])
