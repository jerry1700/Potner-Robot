from setuptools import find_packages, setup

package_name = "potner_llm"

setup(
    name=package_name,
    version="0.1.0",
    packages=find_packages(exclude=["test"]),
    data_files=[
        ("share/ament_index/resource_index/packages", ["resource/" + package_name]),
        ("share/" + package_name, ["package.xml"]),
        ("share/" + package_name + "/config", ["config/llm.yaml"]),
    ],
    install_requires=["setuptools", "requests"],
    zip_safe=True,
    maintainer="E104",
    maintainer_email="notbad1700@gmail.com",
    description="식물 로봇 LLM 클라이언트·프롬프트·툴 허브 (SSAFY GMS / OpenAI 호환)",
    license="MIT",
    entry_points={
        "console_scripts": [
            "potner_llm_chat = potner_llm.cli:main",
        ],
    },
)
