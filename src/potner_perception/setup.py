from glob import glob

from setuptools import find_packages, setup

package_name = "potner_perception"

setup(
    name=package_name,
    version="0.1.0",
    packages=find_packages(exclude=["test"]),
    data_files=[
        ("share/ament_index/resource_index/packages", ["resource/" + package_name]),
        ("share/" + package_name, ["package.xml"]),
        ("share/" + package_name + "/models", glob("models/*.pt")),
    ],
    install_requires=["setuptools"],
    zip_safe=True,
    maintainer="E104",
    maintainer_email="notbad1700@gmail.com",
    description="카메라 영상에서 사람과 ArUco 스테이션 마커를 찾는 인지 계층",
    license="MIT",
    entry_points={
        "console_scripts": [
            "person_detector = potner_perception.person_detector_node:main",
            "scan_presence = potner_perception.scan_presence_node:main",
            "marker_detector = potner_perception.marker_detector_node:main",
            "focal_calibrator = potner_perception.focal_calibrator_node:main",
        ],
    },
)
