"""음성 합성 명령 조립.

ROS에 의존하지 않는 순수 파이썬 모듈입니다.

이 모듈이 따로 있는 이유는 **셸 주입** 때문입니다. 로봇이 말할 문장은
서버의 LLM 이 만들어 MQTT 로 내려보냅니다. 즉 외부에서 온 문자열입니다.
그걸 셸 문자열에 끼워 넣으면 문장에 따옴표나 세미콜론이 섞였을 때
임의의 명령이 실행될 수 있습니다.

그래서 명령을 **인자 배열로만** 조립하고, 실행할 때도 shell=True 를
쓰지 않습니다.
"""

# 파라미터로 받은 명령 배열에서 이 자리에 문장이 들어갑니다.
TEXT_PLACEHOLDER = "{text}"

# espeak-ng 는 한국어 발음이 어색하지만 오프라인이고 어디서나 깔립니다.
# 데모 품질이 필요하면 piper 같은 신경망 TTS 로 바꾸세요. 이 모듈은
# 명령만 조립하므로 파라미터만 교체하면 됩니다.
DEFAULT_COMMAND = ("espeak-ng", "-v", "ko", "-s", "150", TEXT_PLACEHOLDER)


def build_command(template, text: str):
    """명령 배열의 자리표시자를 문장으로 바꿉니다.

    문장은 항상 **하나의 인자**로 들어갑니다. 공백이 있어도 쪼개지지
    않고, 따옴표나 세미콜론이 있어도 셸이 해석하지 않습니다.

    Args:
        template: 명령 배열. 예) ["espeak-ng", "-v", "ko", "{text}"]
        text: 말할 문장

    Returns:
        실행 가능한 인자 배열

    Raises:
        ValueError: 자리표시자가 없거나 배열이 비었을 때
    """
    argv = list(template)
    if not argv:
        raise ValueError("명령 배열이 비어 있습니다")
    if TEXT_PLACEHOLDER not in argv:
        raise ValueError(f"명령 배열에 {TEXT_PLACEHOLDER} 자리표시자가 없습니다")

    return [text if part == TEXT_PLACEHOLDER else part for part in argv]


def normalize_text(text: str, max_length: int = 300) -> str:
    """말할 문장을 다듬습니다.

    줄바꿈은 TTS 엔진이 문장 끝으로 오해하니 공백으로 바꿉니다. 길이를
    자르는 이유는 LLM 이 예상보다 긴 답을 보낼 때 로봇이 몇 분 동안
    혼자 떠드는 걸 막기 위함입니다.
    """
    flattened = " ".join(text.split())
    if len(flattened) <= max_length:
        return flattened
    return flattened[:max_length].rstrip() + "..."


def wav_filename(text: str) -> str:
    """미리 녹음해둔 음성 파일 이름을 만듭니다.

    자주 쓰는 문장(귀가 인사 등)은 미리 녹음해두면 합성 지연이 없고
    품질도 좋습니다. 파일이 있으면 그걸 재생하고, 없으면 합성합니다.

    한글은 str.isalnum() 이 True 를 돌려주므로 따로 처리하지 않습니다.
    """
    safe = "".join(
        char if char.isalnum() else "_" for char in normalize_text(text, 60)
    )
    return f"{safe.strip('_')}.wav"
