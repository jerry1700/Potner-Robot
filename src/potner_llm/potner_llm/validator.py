"""입력(사용자 질문)·출력(LLM 응답) 검증.

- 질문 검증 실패 → InvalidQuestionError를 던진다 (호출자가 에러 응답 반환).
- 응답 검증 실패 → 사유 문자열을 돌려준다 (호출자가 폴백 메시지로 대체).

참고: "JSON이 깨졌는지"는 HTTP 전송 계층의 문제라 client._post_chat에서
파싱·재시도로 처리하고, 여기서는 파싱이 끝난 텍스트의 '내용'만 본다.
"""

from __future__ import annotations

import re
from typing import Optional

from .config import ServiceSettings
from .exceptions import InvalidQuestionError

# 질문에 허용하는 문자: 공백, 한글(자모 포함), 영문/숫자, 일반 문장부호, 이모지
_ALLOWED_QUESTION_CHARS = re.compile(
    r"[\s"
    r"가-힣ᄀ-ᇿ㄰-㆏"   # 한글 음절·자모
    r"A-Za-z0-9"
    r".,!?~%()\[\]{}'\"“”‘’:;·…\-—_/+&*#@^="
    r"☀-➿\U0001F300-\U0001FAFF️‍"  # 이모지
    r"]"
)

# 한글·영문·숫자가 하나라도 있는지 (특수문자만 입력됐는지 판별)
_MEANINGFUL_CHAR = re.compile(r"[가-힣㄰-㆏A-Za-z0-9]")

_HANGUL = re.compile(r"[가-힣]")


def validate_question(text: Optional[str], settings: ServiceSettings) -> str:
    """질문을 검증하고, 통과하면 앞뒤 공백을 정리한 질문을 돌려준다.

    실패 시 InvalidQuestionError(reason, message_ko)를 던진다.
    """
    if text is None:
        raise InvalidQuestionError("empty", "질문이 비어 있어요. 무엇이든 물어봐 주세요!")

    stripped = str(text).strip()
    if not stripped:
        # 빈 문자열과 공백만 있는 경우를 구분해 로그로 원인을 추적할 수 있게 한다.
        reason = "whitespace_only" if text else "empty"
        raise InvalidQuestionError(reason, "질문이 비어 있어요. 무엇이든 물어봐 주세요!")

    if len(stripped) > settings.max_question_length:
        raise InvalidQuestionError(
            "too_long",
            f"질문이 너무 길어요. {settings.max_question_length}자 이내로 줄여 주세요.",
        )

    # 허용 문자 검사를 먼저 한다 — 키릴·아랍 문자처럼 "지원하지 않는 문자"가
    # "특수문자만 입력"으로 잘못 분류되지 않도록.
    unsupported = [ch for ch in stripped if not _ALLOWED_QUESTION_CHARS.fullmatch(ch)]
    if unsupported:
        raise InvalidQuestionError(
            "unsupported_chars",
            "읽을 수 없는 글자가 섞여 있어요. 한국어나 영어로 물어봐 주세요.",
        )

    if not _MEANINGFUL_CHAR.search(stripped):
        raise InvalidQuestionError(
            "special_chars_only", "무슨 뜻인지 잘 모르겠어요. 말로 다시 물어봐 줄래요?"
        )

    return stripped


def check_response(text: Optional[str], settings: ServiceSettings) -> Optional[str]:
    """LLM 응답의 내용 검증. 통과하면 None, 실패하면 사유 코드를 돌려준다.

    사유 코드: empty | too_long | banned_word | prompt_leak | not_korean
    """
    if text is None or not str(text).strip():
        return "empty"

    body = str(text).strip()

    # 후처리에서 문장 단위로 자를 수 있는 수준을 한참 넘긴 응답은
    # 지시를 무시한 것으로 보고 통째로 버린다.
    if len(body) > settings.max_response_length * 4:
        return "too_long"

    lowered = body.lower()
    for word in settings.banned_words:
        if word.lower() in lowered:
            return "banned_word"

    for marker in settings.prompt_leak_markers:
        if marker.lower() in lowered:
            return "prompt_leak"

    # "답변은 한국어" 규칙: 한글이 한 글자도 없으면 위반으로 본다.
    if not _HANGUL.search(body):
        return "not_korean"

    return None
