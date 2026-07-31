from glob import glob

from setuptools import find_packages, setup

package_name = "potner_docking"

setup(
    name=package_name,
    version="0.1.0",
    packages=find_packages(exclude=["test"]),
    data_files=[
        ("share/ament_index/resource_index/packages", ["resource/" + package_name]),
        ("share/" + package_name, ["package.xml"]),
        ("share/" + package_name + "/markers", glob("markers/*.png")),
    ],
    install_requires=["setuptools"],
    zip_safe=True,
    maintainer="E104",
    maintainer_email="notbad1700@gmail.com",
    description="ArUco 마커를 보며 스테이션에 정밀 도킹하는 액션 서버",
    license="MIT",
    entry_points={
        "console_scripts": [
            "docking_server = potner_docking.docking_server_node:main",
        ],
    },
)
