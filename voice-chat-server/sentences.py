"""LLM 텍스트 스트림 → TTS 조각 나누기 (순수 로직).

app.py에서 떼어낸 이유는 **테스트 가능성**이다. app.py는 import 시점에
VoiceChatApp을 만들고(API 키 필수) 필러 합성 스레드를 띄워서(실 GMS 호출),
"테스트는 절대 실제 GMS를 부르지 않는다" 불변식 아래에서는 import조차 할 수
없다. 여기는 re 말고는 아무것도 안 쓰므로 어디서든 안전하게 import된다.

무엇을 하는 모듈인가: LLM이 델타(토막글)로 흘려보내는 답변을, 문장이
완성되는 즉시 TTS로 넘길 수 있게 자르는 규칙들이다. 전체 답변을 기다렸다
합성하면 첫 소리까지 10초를 넘기므로, 이 자르기가 체감 지연을 결정한다.
"""

from __future__ import annotations

import re

# 마크다운 기호 — 웹 챗(초록이) 응답에 섞여 오는데, TTS가 "별표별표"를
# 읽으면 안 되니 벗긴다. 링크는 표시 텍스트만 남긴다.
_MARKDOWN_CHARS = re.compile(r"[*_`#>]+|\[([^\]]*)\]\([^)]*\)")

# 문장 끝 판정 — 구두점 뒤에 붙는 닫는 따옴표/괄호까지 문장에 포함시킨다.
# 줄바꿈도 문장 경계로 본다(목록형 답변 대응).
_SENTENCE_END = re.compile(r"[.!?…。]+[\s\"'）)】\]]*|\n+")

# 첫 조각 조기 절단 기준. 초록이는 구두점 없이 긴 문장을 쓰는 일이 많아,
# 첫 조각만은 문장 완성을 기다리지 않고 이 길이가 모이면 어절 경계에서
# 잘라 TTS를 시작한다.
FIRST_CHUNK_CHARS = 20


def strip_markdown(text: str) -> str:
    """TTS가 기호를 읽지 않게 마크다운을 벗긴다."""
    return _MARKDOWN_CHARS.sub(lambda m: m.group(1) or "", text)


def pop_sentences(buffer: str) -> tuple[list[str], str]:
    """버퍼에서 완성된 문장들을 떼어내고 나머지를 돌려준다."""
    sentences: list[str] = []
    start = 0
    for match in _SENTENCE_END.finditer(buffer):
        sentence = buffer[start : match.end()].strip()
        if sentence:
            sentences.append(sentence)
        start = match.end()
    return sentences, buffer[start:]


def cut_first_chunk(buffer: str) -> tuple[str, str]:
    """쉼표 > 공백 순으로 자연스러운 절단점을 찾아 (첫 조각, 나머지)를 돌려준다."""
    for separator in (", ", ","):
        idx = buffer.rfind(separator, 10)
        if idx > 10:
            cut = idx + len(separator)
            return buffer[:cut].strip(), buffer[cut:]
    idx = buffer.rfind(" ", 10)
    if idx > 10:
        return buffer[:idx].strip(), buffer[idx + 1 :]
    return buffer.strip(), ""
