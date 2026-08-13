from setuptools import find_packages, setup

package_name = "potner_mission"

setup(
    name=package_name,
    version="0.1.0",
    packages=find_packages(exclude=["test"]),
    data_files=[
        ("share/ament_index/resource_index/packages", ["resource/" + package_name]),
        ("share/" + package_name, ["package.xml"]),
    ],
    install_requires=["setuptools"],
    zip_safe=True,
    maintainer="E104",
    maintainer_email="notbad1700@gmail.com",
    description="센서 상태에 따라 임무를 판단하는 상태 머신과 최우선 안전 정지 노드",
    license="MIT",
    entry_points={
        "console_scripts": [
            "mission_manager = potner_mission.mission_manager_node:main",
            "safety = potner_mission.safety_node:main",
            "drive_node = potner_mission.drive_node:main",
            "simple_navigator = potner_mission.simple_navigator_node:main",
            "pose_report = potner_mission.pose_report_node:main",
        ],
    },
)
