"""음성 합성 명령 조립.

ROS에 의존하지 않는 순수 파이썬 모듈입니다.

이 모듈이 따로 있는 이유는 **셸 주입** 때문입니다. 로봇이 말할 문장은
서버의 LLM 이 만들어 MQTT 로 내려보냅니다. 즉 외부에서 온 문자열입니다.
그걸 셸 문자열에 끼워 넣으면 문장에 따옴표나 세미콜론이 섞였을 때
임의의 명령이 실행될 수 있습니다.

그래서 명령을 **인자 배열로만** 조립하고, 실행할 때도 shell=True 를
쓰지 않습니다.

음성 대화(voice-chat-server)는 문장을 직접 넘기는 대신 GMS 로 합성한
오디오 파일을 스풀 디렉터리에 떨어뜨리고 그 **경로**를 넘깁니다. 경로도
외부 입력이라 같은 방어가 필요합니다 — resolve_spool_path 를 보세요.
"""

from pathlib import Path

# 파라미터로 받은 명령 배열에서 이 자리에 문장이 들어갑니다.
TEXT_PLACEHOLDER = "{text}"

# espeak-ng 는 한국어 발음이 어색하지만 오프라인이고 어디서나 깔립니다.
# 데모 품질이 필요하면 piper 같은 신경망 TTS 로 바꾸세요. 이 모듈은
# 명령만 조립하므로 파라미터만 교체하면 됩니다.
DEFAULT_COMMAND = ("espeak-ng", "-v", "ko", "-s", "150", TEXT_PLACEHOLDER)

# 음성 대화 조각을 재생하는 기본 명령. GMS TTS 에 response_format=wav 를
# 요청하므로 aplay 로 충분합니다 (실측 2026-08-03: 200, PCM 16bit mono 24kHz).
# mp3 로 받으면 aplay 가 못 읽으니 mpg123 으로 바꿔야 합니다.
DEFAULT_AUDIO_COMMAND = ("aplay", "-q", TEXT_PLACEHOLDER)


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


def resolve_spool_path(root, raw: str) -> Path:
    """재생할 오디오 파일 경로를 스풀 루트 안으로 제한합니다.

    경로는 음성 서버가 만든 JSON 봉투에서 옵니다. 즉 노드 밖에서 온
    문자열입니다. 그대로 재생기에 넘기면 ``../../../etc/passwd`` 처럼
    아무 파일이나 지목할 수 있고, 재생기가 못 읽는 파일이라도 어떤
    파일이 존재하는지가 로그로 새어 나갑니다.

    build_command 가 셸 주입을 막는 것과 같은 이유로, 경로는 루트
    아래인지 확인하고 벗어나면 거부합니다. 심볼릭 링크로 우회하는 것도
    막으려고 resolve() 로 실제 경로까지 펼친 뒤에 비교합니다.

    Args:
        root: 스풀 루트 디렉터리
        raw: 봉투에 실려 온 경로. 절대경로여도 루트 아래이면 받습니다

    Returns:
        펼쳐진 절대 경로

    Raises:
        ValueError: 비었거나 루트를 벗어날 때
    """
    if not str(raw or "").strip():
        raise ValueError("오디오 경로가 비어 있습니다")
    if not str(root or "").strip():
        raise ValueError("스풀 루트가 설정되지 않았습니다")

    base = Path(root).expanduser().resolve()
    # raw 가 절대경로면 / 연산이 그걸 그대로 채택합니다. 그래서 아래
    # is_relative_to 검사가 유일한 방어선입니다 — 지우지 마세요.
    candidate = (base / Path(str(raw)).expanduser()).resolve()

    if not candidate.is_relative_to(base):
        raise ValueError(f"스풀 루트({base}) 밖의 경로입니다: {raw}")

    return candidate
