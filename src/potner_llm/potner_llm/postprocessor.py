"""LLM 응답 후처리 — 공백·길이 정리와 TTS 친화 변환."""

from __future__ import annotations

import re

from .config import ServiceSettings

_EMOJI = re.compile(
    r"[\U0001F000-\U0001FAFF☀-➿✀-➿️‍]",
)
# TTS가 읽지 못하는 마크다운·장식 기호
_MARKDOWN_NOISE = re.compile(r"[*_`#>|]")
_MULTI_SPACE = re.compile(r"[ \t]{2,}")
_MULTI_NEWLINE = re.compile(r"\n{2,}")
# 문장 끝(마침표·물음표·느낌표 + 공백/끝) 기준 분리. 한국어 구어체의 '~요.' 등 포함.
_SENTENCE_END = re.compile(r"(?<=[.!?…])\s+")


def postprocess(text: str, settings: ServiceSettings) -> str:
    """응답을 화면·TTS에 내보낼 형태로 다듬는다.

    순서: 이모지/장식 제거 → 공백 정리 → 문장 수 제한 → 길이 제한.
    """
    cleaned = text
    if settings.strip_emoji:
        cleaned = _EMOJI.sub("", cleaned)
    cleaned = _MARKDOWN_NOISE.sub("", cleaned)

    cleaned = cleaned.replace("\r\n", "\n")
    cleaned = _MULTI_NEWLINE.sub("\n", cleaned)
    # TTS는 줄바꿈을 못 읽으므로 문장 사이 공백으로 바꾼다.
    cleaned = cleaned.replace("\n", " ")
    cleaned = _MULTI_SPACE.sub(" ", cleaned).strip()

    cleaned = _limit_sentences(cleaned, settings.max_response_sentences)
    cleaned = _limit_length(cleaned, settings.max_response_length)
    return cleaned.strip()


def _limit_sentences(text: str, max_sentences: int) -> str:
    if max_sentences <= 0:
        return text
    sentences = _SENTENCE_END.split(text)
    if len(sentences) <= max_sentences:
        return text
    return " ".join(sentences[:max_sentences])


def _limit_length(text: str, max_length: int) -> str:
    """길이를 넘으면 가능한 한 문장 경계에서 자르고, 안 되면 말줄임표로 끊는다."""
    if max_length <= 0 or len(text) <= max_length:
        return text

    truncated = text[:max_length]
    boundary = max(
        truncated.rfind("."), truncated.rfind("!"), truncated.rfind("?"), truncated.rfind("…")
    )
    # 문장 경계가 너무 앞이면(절반 이하) 정보 손실이 크니 그냥 자르고 말줄임표.
    if boundary >= max_length // 2:
        return truncated[: boundary + 1]
    return truncated.rstrip() + "…"
