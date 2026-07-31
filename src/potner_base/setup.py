from setuptools import find_packages, setup

package_name = "potner_base"

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
    description="Potner 하드웨어 계층 (ESP32 시리얼, 차동 구동 기구학, 오도메트리)",
    license="MIT",
    entry_points={
        "console_scripts": [
            "base_driver = potner_base.base_driver_node:main",
            "plant_sensors = potner_base.plant_sensors_node:main",
            "speaker = potner_base.speaker_node:main",
            "face_display = potner_base.face_display_node:main",
        ],
    },
)
