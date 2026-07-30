"""표정 도형 검증.

웃는 얼굴과 우는 얼굴이 뒤바뀌어도 코드는 멀쩡히 돌아갑니다. 화면을 봐야만
알 수 있는 종류의 버그라, 부호를 여기서 못박습니다.

예전에 초점거리 공식이 뒤집힌 채 CI 를 통과한 적이 있습니다. 그때 계산이
노드 파일 안에 있어서 테스트가 닿지 못한 것이 원인이었고, 그 뒤로 계산
로직은 순수 모듈에 두는 것을 규칙으로 삼았습니다.
"""

import pytest

from potner_base.face import (
    DEFAULT_EXPRESSION,
    HAPPY,
    NEUTRAL,
    SAD,
    VERY_HAPPY,
    face_for,
    mouth_points,
    parse_drm_modes,
    scale,
    to_pixels,
    viewport_for,
)

ALL_EXPRESSIONS = [VERY_HAPPY, HAPPY, NEUTRAL, SAD]


def test_기쁠수록_입이_더_웃는다():
    """곡률 순서가 뒤집히면 슬플 때 웃는 얼굴이 나옵니다."""
    curvatures = [face_for(name).mouth_curvature for name in ALL_EXPRESSIONS]

    assert curvatures == sorted(curvatures, reverse=True), (
        f"VERY_HAPPY > HAPPY > NEUTRAL > SAD 순서여야 합니다: {curvatures}"
    )


def test_웃는_입은_가운데가_아래로_처진다():
    """★ y 축은 아래로 증가합니다.

    이미지 좌표계라 웃는 입은 가운데 점의 y 가 입꼬리보다 **커야** 합니다.
    여기서 부호를 뒤집는 것이 이 기능에서 가장 하기 쉬운 실수입니다.
    """
    left, middle, right = mouth_points(face_for(VERY_HAPPY))

    assert middle[1] > left[1]
    assert middle[1] > right[1]


def test_찡그린_입은_가운데가_위로_올라간다():
    left, middle, right = mouth_points(face_for(SAD))

    assert middle[1] < left[1]
    assert middle[1] < right[1]


def test_무표정은_일자_입이다():
    left, middle, right = mouth_points(face_for(NEUTRAL))

    assert middle[1] == pytest.approx(left[1])
    assert middle[1] == pytest.approx(right[1])


@pytest.mark.parametrize("name", ALL_EXPRESSIONS)
def test_입꼬리는_좌우_대칭이다(name):
    left, middle, right = mouth_points(face_for(name))

    assert left[1] == pytest.approx(right[1])
    assert middle[0] == pytest.approx((left[0] + right[0]) / 2)


@pytest.mark.parametrize("name", ALL_EXPRESSIONS)
def test_눈이_좌우_대칭이다(name):
    face = face_for(name)

    assert face.left_eye.center_y == pytest.approx(face.right_eye.center_y)
    assert face.left_eye.radius == pytest.approx(face.right_eye.radius)
    assert face.left_eye.openness == pytest.approx(face.right_eye.openness)
    assert face.left_eye.center_x < face.right_eye.center_x


@pytest.mark.parametrize("name", ALL_EXPRESSIONS)
def test_모든_도형이_화면_안에_있다(name):
    """정규화 좌표가 0~1 을 벗어나면 얼굴이 화면 밖으로 잘립니다."""
    face = face_for(name)

    for eye in (face.left_eye, face.right_eye):
        assert 0.0 < eye.center_x - eye.radius
        assert eye.center_x + eye.radius < 1.0
        assert 0.0 < eye.center_y - eye.radius
        assert eye.center_y + eye.radius < 1.0

    for x, y in mouth_points(face):
        assert 0.0 < x < 1.0, f"{name} 입이 가로로 벗어났습니다: {x}"
        assert 0.0 < y < 1.0, f"{name} 입이 세로로 벗어났습니다: {y}"


def test_입이_눈보다_아래에_있다():
    for name in ALL_EXPRESSIONS:
        face = face_for(name)
        mouth_top = min(y for _, y in mouth_points(face))
        eye_bottom = max(
            eye.center_y + eye.radius for eye in (face.left_eye, face.right_eye)
        )
        assert mouth_top > eye_bottom, f"{name} 입이 눈에 겹칩니다"


def test_활짝_웃으면_눈이_감긴다():
    """입만 키우면 무섭게 보입니다."""
    assert face_for(VERY_HAPPY).left_eye.openness < face_for(HAPPY).left_eye.openness


def test_모르는_표정은_기본_표정으로_떨어진다():
    """서버가 값을 늘릴 수 있습니다. 그때 로봇을 고치지 않아도 되어야 합니다."""
    assert face_for("EXCITED").expression == DEFAULT_EXPRESSION
    assert face_for("").expression == DEFAULT_EXPRESSION
    assert face_for(None).expression == DEFAULT_EXPRESSION
    assert face_for("happy").expression == DEFAULT_EXPRESSION  # 소문자


def test_기본_표정은_무표정이다():
    """서버가 모르는 값을 보냈을 때 웃거나 울면 사용자가 오해합니다."""
    assert DEFAULT_EXPRESSION == NEUTRAL


def test_정사각_영역을_가운데_잡는다():
    """가로세로를 그대로 곱하면 16:9 화면에서 얼굴이 가로로 늘어납니다.
    짧은 쪽을 한 변으로 하는 정사각형을 가운데 둡니다."""
    view = viewport_for(1024, 600)  # 7인치 LCD

    assert view.size == 600
    assert view.x == 212  # (1024 - 600) // 2
    assert view.y == 0


def test_정사각_화면이면_여백이_없다():
    view = viewport_for(600, 600)
    assert (view.x, view.y, view.size) == (0, 0, 600)


def test_세로가_긴_화면도_처리한다():
    view = viewport_for(600, 1024)
    assert view.size == 600
    assert (view.x, view.y) == (0, 212)


def test_픽셀_변환():
    view = viewport_for(1024, 600)

    assert to_pixels((0.5, 0.5), view) == (212 + 300, 300)
    assert to_pixels((0.0, 0.0), view) == (212, 0)
    assert to_pixels((1.0, 1.0), view) == (212 + 600, 600)


def test_길이는_원점을_더하지_않는다():
    """반지름이나 선 굵기에 여백을 더하면 눈이 화면을 덮습니다."""
    view = viewport_for(1024, 600)
    assert scale(0.075, view) == 45


def test_해상도_감지():
    """/sys/class/drm/<커넥터>/modes 의 첫 줄이 선호 모드입니다."""
    modes = "\n".join(["1024x600", "800x600", "640x480", ""])

    assert parse_drm_modes(modes) == (1024, 600)
    assert parse_drm_modes("1920x1080") == (1920, 1080)


def test_읽을_수_없는_줄은_건너뛴다():
    """커넥터 이름이나 빈 줄이 섞여 있어도 해상도를 찾아야 합니다."""
    modes = "\n".join(["", "  ", "1024x600"])

    assert parse_drm_modes(modes) == (1024, 600)


@pytest.mark.parametrize(
    "text",
    ["", "\n\n", "알 수 없음", "x", "1024x", "x600", "abcxdef", "0x0"],
)
def test_해상도를_못_읽으면_None(text):
    """못 읽었는데 0 이나 엉뚱한 값을 돌려주면 얼굴이 화면 밖으로 나갑니다."""
    assert parse_drm_modes(text) is None


def test_얼굴이_화면_안에_들어간다():
    """정규화 좌표가 0~1 이므로 뷰포트 안에 반드시 들어가야 합니다."""
    view = viewport_for(1024, 600)
    face = face_for(VERY_HAPPY)

    for point in mouth_points(face):
        x, y = to_pixels(point, view)
        assert view.x <= x <= view.x + view.size
        assert view.y <= y <= view.y + view.size


def test_표정_이름이_서버_명세와_같다():
    """potner_base 는 potner_bridge 에 의존하지 않으려고 문자열을 다시
    정의합니다. 그래서 어긋날 수 있어 여기서 대조합니다."""
    from potner_bridge.telemetry import KNOWN_EXPRESSIONS

    assert set(ALL_EXPRESSIONS) == set(KNOWN_EXPRESSIONS)


def test_기본_표정도_양쪽이_같다():
    from potner_bridge.telemetry import DEFAULT_EXPRESSION as BRIDGE_DEFAULT

    assert DEFAULT_EXPRESSION == BRIDGE_DEFAULT
