"""로봇의 현재 자세를 사람이 옮겨 적을 수 있는 형태로 만드는 순수 로직.

ROS 에 의존하지 않으므로 CI 에서 검증됩니다.

★ 왜 필요한가 — 좌표의 출처는 서버의 ``robot_location`` 하나이고, 로봇은
  좌표를 저장하지 않습니다. 그래서 자리를 등록하려면 **로봇을 그 자리로
  데려간 뒤 지금 좌표를 읽어 서버에 넣어야** 합니다.

  그런데 ``/odom`` 이 주는 것은 쿼터니언이라 사람이 읽을 수 없습니다.
  손으로 yaw 를 환산하다 부호나 절반각을 틀리면, 로봇이 등록한 자리에서
  엉뚱한 방향을 보고 서게 됩니다 — 스테이션 앞이면 마커를 아예 못 봅니다.

  그래서 서버 API(``PUT /locations/{type}/pose``)가 그대로 받는 형태로
  만들어 둡니다. 복사해서 붙이면 끝나게 하는 것이 목적입니다.
"""

import json
import math

# 서버의 pose 컬럼이 소수점 이하 세 자리까지입니다 (DEVICE-MQTT.md 의
# robot_location 참고). 그보다 길게 보내면 어차피 잘리는데, 화면에 20자리
# 소수가 뜨면 사람이 옮겨 적다가 자리를 틀립니다.
DIGITS = 3

# yaw 의 허용 범위는 -pi ~ pi 입니다. DB 제약(ck_robot_location_pose)이
# 이 범위를 강제하므로, 여기서 접어 두지 않으면 등록 요청이 거부됩니다.
YAW_LIMIT = math.pi


def yaw_from_quaternion(x: float, y: float, z: float, w: float) -> float:
    """쿼터니언에서 yaw(rad)를 뽑습니다."""
    return math.atan2(2.0 * (w * z + x * y), 1.0 - 2.0 * (y * y + z * z))


def wrap_yaw(yaw: float) -> float:
    """yaw 를 -pi ~ pi 로 접습니다."""
    wrapped = math.atan2(math.sin(yaw), math.cos(yaw))
    # 반올림이 경계를 넘길 수 있습니다 (3.14159... -> 3.142 > pi).
    # DB 제약이 ±3.1416 이라 이건 그대로 거부됩니다.
    return max(-YAW_LIMIT, min(YAW_LIMIT, wrapped))


def pose_payload(x: float, y: float, yaw: float) -> dict:
    """서버 ``PUT /locations/{type}/pose`` 가 그대로 받는 본문."""
    return {
        "x": round(x, DIGITS),
        "y": round(y, DIGITS),
        "yaw": round(wrap_yaw(yaw), DIGITS),
    }


def pose_json(x: float, y: float, yaw: float) -> str:
    """위 본문을 그대로 붙여넣을 수 있는 한 줄 JSON 으로."""
    return json.dumps(pose_payload(x, y, yaw), separators=(",", ":"))


def pose_summary(x: float, y: float, yaw: float) -> str:
    """사람이 읽는 한 줄. 각도를 도 단위로 함께 보여줍니다.

    라디안만 보여주면 방향이 맞는지 눈으로 판단할 수 없어서, 등록해 놓고
    나중에야 로봇이 반대를 보고 선다는 걸 알게 됩니다.
    """
    body = pose_payload(x, y, yaw)
    degrees = math.degrees(body["yaw"])
    return (
        f'x={body["x"]:.3f} y={body["y"]:.3f} '
        f'yaw={body["yaw"]:.3f}rad ({degrees:.0f}도)  ->  {pose_json(x, y, yaw)}'
    )
