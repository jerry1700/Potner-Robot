"""CI에서 ROS 없이 순수 로직만 테스트하기 위한 경로 설정.

젠킨스 서버에는 ROS 2가 없습니다. 그래서 rclpy를 import 하는 노드 파일은
테스트하지 않고, 계산 로직만 담긴 모듈을 골라서 검증합니다.

노드 파일과 계산 로직을 파일 단위로 분리해 둔 이유가 이것입니다.
"""

import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"

# 패키지 목록을 손으로 적어두면 새 패키지를 만들 때마다 import 오류가
# 납니다. src/potner_* 를 훑어서 자동으로 올립니다.
for package in sorted(SRC.glob("potner_*")):
    if (package / package.name).is_dir():
        sys.path.insert(0, str(package))
