"""표정 도형 계산.

ROS 도 OpenCV 도 쓰지 않는 순수 모듈입니다. 화면 없이 CI 에서 검증됩니다.

좌표는 모두 **0~1 정규화 값**입니다. 화면 크기를 곱하면 픽셀이 됩니다.
지금은 HDMI 모니터에 그리지만 나중에 7인치 LCD 로 옮겨야 하는데, 해상도가
달라도 이 파일은 그대로 씁니다.

y 축은 **아래로 증가**합니다 (이미지 좌표계). 웃는 입은 가운데가 입꼬리보다
아래에 있으므로 y 가 더 큽니다. 이 부호를 뒤집는 실수가 웃는 얼굴과 우는
얼굴을 맞바꾸는데, 화면을 봐야만 알 수 있는 종류라 계산을 여기로 빼서
테스트가 잡게 했습니다. (초점거리 공식이 뒤집힌 채 CI 를 통과한 적이 있어
계산 로직은 순수 모듈에 두는 것을 규칙으로 삼았습니다.)
"""

from dataclasses import dataclass

# potner_bridge.telemetry.Expression 과 같은 문자열이어야 합니다.
# 여기서 다시 정의하는 이유는 potner_base 가 potner_bridge 에 의존하지 않게
# 하려는 것입니다. 서버와 끊겨도 얼굴은 그려야 합니다.
# tests/test_face.py 가 양쪽을 대조합니다.
VERY_HAPPY = "VERY_HAPPY"
HAPPY = "HAPPY"
NEUTRAL = "NEUTRAL"
SAD = "SAD"

DEFAULT_EXPRESSION = NEUTRAL


@dataclass(frozen=True)
class Eye:
    center_x: float
    center_y: float
    radius: float
    # 1.0 은 완전히 뜬 눈, 0.0 은 감은 눈(선). 활짝 웃을 때 눈이 감깁니다.
    openness: float


@dataclass(frozen=True)
class Face:
    expression: str
    left_eye: Eye
    right_eye: Eye
    # +1 활짝 웃는 입, 0 일자 입, -1 찡그린 입.
    mouth_curvature: float
    mouth_width: float
    mouth_y: float


CENTER_X = 0.5
EYE_Y = 0.38
EYE_X_OFFSET = 0.18
EYE_RADIUS = 0.075
MOUTH_Y = 0.62


def _face(expression: str, curvature: float, mouth_width: float, openness: float) -> Face:
    """좌우 대칭인 얼굴을 만듭니다.

    눈을 하나씩 따로 적지 않는 이유는, 한쪽만 고치는 실수로 얼굴이 비뚤어지는
    것을 막기 위해서입니다.
    """
    return Face(
        expression=expression,
        left_eye=Eye(CENTER_X - EYE_X_OFFSET, EYE_Y, EYE_RADIUS, openness),
        right_eye=Eye(CENTER_X + EYE_X_OFFSET, EYE_Y, EYE_RADIUS, openness),
        mouth_curvature=curvature,
        mouth_width=mouth_width,
        mouth_y=MOUTH_Y,
    )


# 표정 네 가지. 서버가 값을 늘리면 여기에 추가하고, 그때까지 모르는 값은
# face_for() 가 기본 표정으로 떨어뜨립니다.
_FACES = {
    # 활짝 웃을 때는 눈이 감깁니다. 입만 키우면 무섭게 보입니다.
    VERY_HAPPY: _face(VERY_HAPPY, curvature=1.0, mouth_width=0.30, openness=0.15),
    HAPPY: _face(HAPPY, curvature=0.55, mouth_width=0.28, openness=1.0),
    NEUTRAL: _face(NEUTRAL, curvature=0.0, mouth_width=0.22, openness=1.0),
    SAD: _face(SAD, curvature=-0.5, mouth_width=0.24, openness=0.7),
}


def face_for(expression) -> Face:
    """표정 이름에 맞는 얼굴을 돌려줍니다.

    **모르는 값이면 기본 표정을 돌려줍니다.** 서버가 표정을 늘릴 수 있으므로
    예외를 내지 않습니다 (DEVICE-MQTT.md 9절).
    """
    return _FACES.get(expression, _FACES[DEFAULT_EXPRESSION])


def mouth_points(face: Face):
    """입을 그릴 세 점을 정규화 좌표로 돌려줍니다.

    왼쪽 입꼬리, 가운데, 오른쪽 입꼬리 순서입니다. 이 세 점으로 곡선을
    그립니다.

    y 는 아래로 증가하므로 **웃는 입(curvature > 0)은 가운데 y 가 입꼬리보다
    큽니다.** 여기서 부호를 뒤집으면 기쁠 때 우는 얼굴이 나옵니다.
    """
    half = face.mouth_width / 2.0
    # 곡률 1.0 일 때 입 너비의 절반만큼 처지게 둡니다. 이보다 크면 입이
    # 화면 아래로 나갑니다.
    sag = face.mouth_curvature * half

    return (
        (CENTER_X - half, face.mouth_y),
        (CENTER_X, face.mouth_y + sag),
        (CENTER_X + half, face.mouth_y),
    )


def to_pixels(point, width: int, height: int):
    """정규화 좌표를 픽셀로 바꿉니다.

    가로세로를 따로 곱하므로 화면 비율이 달라지면 얼굴도 늘어납니다. 7인치
    LCD 로 옮길 때 비율이 많이 다르면 짧은 쪽 기준으로 정사각형 영역을 잡아
    쓰는 편이 낫습니다.
    """
    x, y = point
    return int(round(x * width)), int(round(y * height))
