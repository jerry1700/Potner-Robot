"""좌표 등록용 자세 보고 검증.

여기서 틀리면 등록한 자리에서 로봇이 엉뚱한 방향을 보고 선다. 스테이션
앞이면 마커를 아예 못 봐서 도킹이 통째로 실패한다.
"""

import json
import math

import pytest

from potner_mission.pose_report import (
    DIGITS,
    pose_json,
    pose_payload,
    pose_summary,
    wrap_yaw,
    yaw_from_quaternion,
)


def quaternion_for(yaw_deg):
    """평면 회전만 있는 쿼터니언 (z, w)."""
    half = math.radians(yaw_deg) / 2.0
    return 0.0, 0.0, math.sin(half), math.cos(half)


@pytest.mark.parametrize("yaw_deg", [0.0, 45.0, 90.0, 179.0, -90.0, -179.0])
def test_쿼터니언에서_각도를_되찾는다(yaw_deg):
    """손으로 환산하다 절반각이나 부호를 틀리는 것을 막는다."""
    assert math.degrees(yaw_from_quaternion(*quaternion_for(yaw_deg))) == (
        pytest.approx(yaw_deg, abs=1e-9)
    )


def test_서버가_받는_키만_담는다():
    """PUT /locations/{type}/pose 가 x, y, yaw 셋을 요구한다.
    하나라도 빠지면 그 자리는 좌표 미설정으로 남는다."""
    assert set(pose_payload(1.0, 2.0, 0.5)) == {"x", "y", "yaw"}


def test_소수점_자리를_줄여_준다():
    """서버 컬럼이 소수 셋째 자리까지다. 화면에 20자리가 뜨면 사람이
    옮겨 적다가 자리를 틀린다."""
    body = pose_payload(1.23456789, -0.98765432, 0.55555555)

    assert body["x"] == pytest.approx(round(1.23456789, DIGITS))
    assert len(str(body["x"]).split(".")[-1]) <= DIGITS


@pytest.mark.parametrize(
    "raw, expected_deg",
    [
        (math.radians(370.0), 10.0),
        (math.radians(-370.0), -10.0),
        (math.radians(200.0), -160.0),
    ],
)
def test_각도를_pi_범위로_접는다(raw, expected_deg):
    """DB 제약(ck_robot_location_pose)이 ±pi 를 강제한다. 안 접으면
    등록 요청이 거부된다."""
    assert math.degrees(wrap_yaw(raw)) == pytest.approx(expected_deg, abs=1e-9)


def test_접은_각도가_pi를_넘지_않는다():
    """반올림이 경계를 넘길 수 있다 — 3.14159 를 세 자리로 줄이면
    3.142 가 되어 pi 보다 크다."""
    for raw in (math.pi, -math.pi, math.pi - 1e-12, math.pi + 1e-12):
        assert abs(pose_payload(0.0, 0.0, raw)["yaw"]) <= round(math.pi, DIGITS)


def test_붙여넣을_수_있는_JSON이다():
    body = json.loads(pose_json(1.5, -0.25, math.radians(90.0)))

    assert body["x"] == pytest.approx(1.5)
    assert body["y"] == pytest.approx(-0.25)
    assert body["yaw"] == pytest.approx(round(math.pi / 2.0, DIGITS))


def test_사람이_읽는_줄에_도_단위가_함께_나온다():
    """라디안만 보여주면 방향이 맞는지 눈으로 판단할 수 없어서, 등록해
    놓고 나중에야 로봇이 반대를 보고 선다는 걸 알게 된다."""
    line = pose_summary(0.0, 0.0, math.radians(-90.0))

    assert "-90도" in line
    assert '"yaw"' in line  # 붙여넣을 JSON 도 같은 줄에 있다
