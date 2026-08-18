"""LLM 델타 → TTS 조각 나누기(sentences.py) 단위 테스트.

이 로직은 원래 voice-chat-server/app.py 안에 있었는데, app.py는 import
시점에 API 키를 요구하고 필러 합성 스레드(실 GMS 호출)를 띄워서 테스트가
import할 수 없었다. 순수 모듈로 떼어낸 뒤 여기서 검증한다 — 네트워크 없음.
"""

from __future__ import annotations

import sys
from pathlib import Path

# voice-chat-server는 colcon 패키지가 아니라 conftest가 올려주지 않는다
# (tests/test_audio_sink.py와 같은 패턴).
SERVER_DIR = Path(__file__).resolve().parent.parent / "voice-chat-server"
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

from sentences import (  # noqa: E402
    FIRST_CHUNK_CHARS,
    cut_first_chunk,
    pop_sentences,
    strip_markdown,
)


# --- pop_sentences: 문장 경계 자르기 ---


def test_pop_sentences_splits_completed_sentences():
    sentences, tail = pop_sentences("안녕하세요. 오늘 날씨가 좋네요! 그런데")
    assert sentences == ["안녕하세요.", "오늘 날씨가 좋네요!"]
    assert tail == "그런데"


def test_pop_sentences_keeps_incomplete_tail():
    """문장이 안 끝났으면 아무것도 떼지 않고 전부 tail로 남긴다."""
    sentences, tail = pop_sentences("아직 문장이 끝나지 않았")
    assert sentences == []
    assert tail == "아직 문장이 끝나지 않았"


def test_pop_sentences_empty_buffer():
    assert pop_sentences("") == ([], "")


def test_pop_sentences_korean_and_ellipsis_punctuation():
    """한국어 답변에 자주 나오는 。와 … 도 문장 끝으로 본다."""
    sentences, tail = pop_sentences("그렇습니다。 글쎄요… 다음은")
    assert sentences == ["그렇습니다。", "글쎄요…"]
    assert tail == "다음은"


def test_pop_sentences_closing_quote_belongs_to_sentence():
    """구두점 뒤에 붙는 닫는 따옴표/괄호는 앞 문장에 포함시킨다 —
    떼어놓으면 다음 조각이 따옴표로 시작해 TTS가 어색해진다."""
    sentences, tail = pop_sentences('그는 "좋아요." 라고 했다.')
    assert sentences[0] == '그는 "좋아요."'
    assert sentences[1] == "라고 했다."
    assert tail == ""


def test_pop_sentences_newline_is_boundary():
    """목록형 답변 대응 — 줄바꿈도 문장 경계다."""
    sentences, tail = pop_sentences("첫째 물주기\n둘째 햇빛")
    assert sentences == ["첫째 물주기"]
    assert tail == "둘째 햇빛"


def test_pop_sentences_streaming_accumulation():
    """실사용 모양 그대로: 델타를 tail에 이어 붙이며 반복 호출한다."""
    deltas = ["오늘은 물을 ", "주지 않아도 돼요. 흙이 ", "아직 촉촉하거든요. 내일"]
    collected: list[str] = []
    tail = ""
    for delta in deltas:
        sentences, tail = pop_sentences(tail + delta)
        collected.extend(sentences)
    assert collected == ["오늘은 물을 주지 않아도 돼요.", "흙이 아직 촉촉하거든요."]
    assert tail == "내일"


# --- cut_first_chunk: 첫 조각 조기 절단 ---


def test_cut_first_chunk_prefers_comma_over_space():
    """쉼표가 있으면 공백보다 우선한다. rfind라 **마지막** 쉼표에서 자른다 —
    이 함수는 버퍼가 조기절단 기준(~20자)에 갓 도달했을 때 불리므로,
    가장 뒤의 자연스러운 절단점을 잡아 첫 조각을 최대한 길게 만든다."""
    head, rest = cut_first_chunk("물은 이틀에 한 번이면 충분해요, 지금은 아직 촉촉하니까요")
    assert head == "물은 이틀에 한 번이면 충분해요,"
    assert rest == "지금은 아직 촉촉하니까요"


def test_cut_first_chunk_uses_last_cut_point():
    """절단점이 여럿이면 마지막 것을 쓴다 (rfind 의미 고정)."""
    head, rest = cut_first_chunk("하나 있고요, 둘도 있고요, 셋은 나중에")
    assert head == "하나 있고요, 둘도 있고요,"
    assert rest == "셋은 나중에"


def test_cut_first_chunk_falls_back_to_space():
    head, rest = cut_first_chunk("쉼표가 하나도 없는 아주 긴 문장이 계속 이어진다")
    assert head  # 어절 경계에서 잘렸다
    assert rest
    assert not head.endswith(" ")
    assert (head + " " + rest) == "쉼표가 하나도 없는 아주 긴 문장이 계속 이어진다"


def test_cut_first_chunk_short_buffer_returns_whole():
    """절단점(>10자)이 없으면 통째로 첫 조각이 된다."""
    head, rest = cut_first_chunk("짧은문장")
    assert head == "짧은문장"
    assert rest == ""


def test_cut_first_chunk_never_cuts_too_early():
    """10자 이전에서 자르면 조각이 너무 짧아 TTS 호출만 낭비된다."""
    head, rest = cut_first_chunk("가나다, 라마바사아자차카타파하 그리고 더")
    # 3번째 글자의 쉼표(idx 3 <= 10)는 무시하고 더 뒤의 공백에서 자른다.
    assert len(head) > 10


def test_first_chunk_chars_is_reasonable():
    """조기 절단 기준이 절단점 최소 위치(10자)보다는 커야 로직이 성립한다."""
    assert FIRST_CHUNK_CHARS > 10


# --- strip_markdown: TTS가 기호를 읽지 않게 ---


def test_strip_markdown_removes_emphasis_and_headers():
    assert strip_markdown("**중요**한 `내용`") == "중요한 내용"
    assert strip_markdown("# 제목\n> 인용") == " 제목\n 인용"


def test_strip_markdown_keeps_link_text_only():
    assert strip_markdown("[식물 도감](https://example.com)을 보세요") == "식물 도감을 보세요"


def test_strip_markdown_plain_text_unchanged():
    assert strip_markdown("평범한 문장은 그대로.") == "평범한 문장은 그대로."


def test_strip_markdown_then_pop_sentences_pipeline():
    """app.py 실제 순서: 문장 완성 → strip → TTS. 같이 써도 어긋나지 않는지."""
    sentences, _ = pop_sentences("**물주기**는 중요해요. 다음 문장.")
    assert strip_markdown(sentences[0]).strip() == "물주기는 중요해요."
