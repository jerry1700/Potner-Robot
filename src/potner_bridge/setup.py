from setuptools import find_packages, setup

package_name = "potner_bridge"

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
    description="Mosquitto MQTT 브로커와 ROS 2 토픽을 잇는 외부 연동 계층",
    license="MIT",
    entry_points={
        "console_scripts": [
            "mqtt_bridge = potner_bridge.mqtt_bridge_node:main",
        ],
    },
)
