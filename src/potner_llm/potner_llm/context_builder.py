"""LLM에 넘길 대화 컨텍스트(시스템 프롬프트 + 메시지 리스트) 조립.

시스템 프롬프트에는 로봇 페르소나 + 식물 상태 요약 + 사용자 정보 같은
'신뢰할 수 있는 쪽에서 만든' 컨텍스트만 넣고, 사용자 발화는 항상 별도의
user 메시지로 유지한다 — 시스템 프롬프트에 섞으면 프롬프트 주입 표면이
넓어진다 (service.py 쪽 prompt_builder와 같은 원칙).

히스토리 절단은 메시지 개수만 세던 기존 방식 대신 두 가지를 함께 본다:
  1) '교환(exchange)' 단위 절단 — user 발화에서 시작해 다음 user 발화
     직전까지를 한 덩어리로 취급한다. tool_calls를 담은 assistant 메시지와
     그에 대응하는 tool 응답이 같은 덩어리에 있으므로, 쌍의 중간이 잘려
     API에 비정합 시퀀스가 넘어가는 일이 없다.
  2) 문자 수 기반 근사 토큰 예산 — 턴 수가 적어도 메시지가 아주 길면
     프롬프트가 무한정 커지던 문제를 막는다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Optional

from .client import ChatMessage
from .prompts import SYSTEM_PLANT

# 전체 컨텍스트(시스템 + 히스토리 + 이번 질문)에 허용하는 근사 토큰 기본값.
# gpt-4.1-nano 계열의 여유 있는 입력 한도 대비 보수적으로 잡았다 — 응답
# 품질보다 매 턴 비용/지연이 커지는 걸 막는 게 목적이다.
DEFAULT_MAX_CONTEXT_TOKENS = 3000

# 메시지 1개당 role/포맷 오버헤드 근사치 (OpenAI 포맷 기준 경험값).
_PER_MESSAGE_OVERHEAD_TOKENS = 4


def estimate_tokens(text: Optional[str]) -> int:
    """정확한 토크나이저 없이 쓰는 보수적 근사치.

    영문/숫자/기호는 약 4자당 1토큰, 한글 등 비ASCII 문자는 자당 1토큰으로
    잡는다 — 실제보다 크게 잡히는 쪽이라 예산 초과로 잘리는 실수는 없다.
    """
    if not text:
        return 0
    ascii_chars = sum(1 for ch in text if ord(ch) < 128)
    other_chars = len(text) - ascii_chars
    return max(1, ascii_chars // 4 + other_chars)


def message_tokens(message: ChatMessage) -> int:
    total = _PER_MESSAGE_OVERHEAD_TOKENS + estimate_tokens(message.content)
    if message.tool_calls:
        total += estimate_tokens(json.dumps(message.tool_calls, ensure_ascii=False))
    return total


def _split_exchanges(history: list[ChatMessage]) -> list[list[ChatMessage]]:
    """user 발화 기준으로 히스토리를 교환 단위 블록으로 나눈다.

    user가 아닌 메시지(assistant/tool)는 항상 직전 블록에 붙는다. 따라서
    assistant의 tool_calls와 그 tool 응답들은 같은 블록에 묶여, 블록 단위로
    자르는 한 쌍이 중간에서 끊기지 않는다. 히스토리가 user가 아닌 메시지로
    시작하는 경우(백엔드 복원 데이터가 어긋난 경우)도 선두 블록으로 묶어
    통째로 남기거나 통째로 버린다.
    """
    blocks: list[list[ChatMessage]] = []
    current: list[ChatMessage] = []
    for message in history:
        if message.role == "user" and current:
            blocks.append(current)
            current = [message]
        else:
            current.append(message)
    if current:
        blocks.append(current)
    return blocks


def trim_history(
    history: list[ChatMessage],
    *,
    max_messages: int = 0,
    max_tokens: int = 0,
) -> list[ChatMessage]:
    """최근 교환부터 예산(메시지 수/근사 토큰) 안에 들어오는 만큼 남긴다.

    max_messages/max_tokens가 0 이하이면 해당 제한은 무시한다.
    가장 최근 교환은 예산을 넘더라도 통째로 남긴다 — 직전 문맥이 아예
    사라지는 것보다 약간의 예산 초과가 낫고, 쌍이 잘리는 일도 없다.
    """
    if not history:
        return []

    selected: list[list[ChatMessage]] = []
    used_messages = 0
    used_tokens = 0
    for block in reversed(_split_exchanges(list(history))):
        block_tokens = sum(message_tokens(m) for m in block)
        over_messages = max_messages > 0 and used_messages + len(block) > max_messages
        over_tokens = max_tokens > 0 and used_tokens + block_tokens > max_tokens
        if selected and (over_messages or over_tokens):
            break
        selected.append(block)
        used_messages += len(block)
        used_tokens += block_tokens
        if over_messages or over_tokens:
            # 첫(가장 최근) 블록만으로 이미 예산 초과 - 더 오래된 건 안 본다.
            break

    return [message for block in reversed(selected) for message in block]


@dataclass
class ConversationContext:
    """LLM 호출 한 번에 필요한 재료 일습.

    system_prompt는 chat_with_tools(system=...)로, messages는 히스토리
    자리에 그대로 넘기면 된다 (messages 마지막이 이번 사용자 발화).
    """

    system_prompt: str
    messages: list[ChatMessage] = field(default_factory=list)
    dropped_messages: int = 0


class ContextBuilder:
    """페르소나 + 식물 상태 + 사용자 정보 + 최근 대화를 한 번에 조립한다."""

    def __init__(
        self,
        *,
        base_system: str = SYSTEM_PLANT,
        max_history_turns: int = 10,
        max_context_tokens: int = DEFAULT_MAX_CONTEXT_TOKENS,
    ) -> None:
        self.base_system = base_system
        self.max_history_turns = max_history_turns
        self.max_context_tokens = max_context_tokens

    def build(
        self,
        *,
        history: list[ChatMessage],
        user_text: str,
        status_dict: Optional[dict[str, Any]] = None,
        user_profile: Optional[dict[str, Any]] = None,
        interaction_notes: Optional[list[str]] = None,
    ) -> ConversationContext:
        user_message = ChatMessage(role="user", content=user_text)
        system_prompt = self._system_prompt(status_dict, user_profile, interaction_notes)

        # 시스템 프롬프트와 이번 질문이 먼저 예산을 차지하고, 남는 만큼만
        # 히스토리에 배분한다. 남는 게 없어도 trim_history가 가장 최근
        # 교환은 남겨 주므로 직전 문맥은 유지된다.
        history_budget = 0
        if self.max_context_tokens > 0:
            history_budget = max(
                1,
                self.max_context_tokens
                - estimate_tokens(system_prompt)
                - message_tokens(user_message),
            )

        trimmed = trim_history(
            history,
            max_messages=self.max_history_turns * 2,
            max_tokens=history_budget,
        )
        dropped = len(history) - len(trimmed)
        if dropped > 0:
            # 잘려나간 과거가 있음을 모델에 알려 "처음 보는 사이"처럼
            # 굴지 않게 한다. 내용 요약이 아니라 존재 사실만 넣는다.
            system_prompt = (
                f"{system_prompt}\n\n[대화 메모]\n"
                f"- 이 앞에 이전 대화 {dropped}개 메시지가 더 있었지만 생략됐다. "
                f"사용자와는 이미 대화를 나눠 온 사이다."
            )

        return ConversationContext(
            system_prompt=system_prompt,
            messages=trimmed + [user_message],
            dropped_messages=dropped,
        )

    def _system_prompt(
        self,
        status_dict: Optional[dict[str, Any]],
        user_profile: Optional[dict[str, Any]],
        interaction_notes: Optional[list[str]],
    ) -> str:
        parts = [self.base_system]
        if status_dict:
            parts.append(f"[현재 내 상태]\n{status_dict}")
        if user_profile:
            profile_lines = "\n".join(f"- {key}: {value}" for key, value in user_profile.items())
            parts.append(f"[사용자 정보]\n{profile_lines}")
        if interaction_notes:
            note_lines = "\n".join(f"- {note}" for note in interaction_notes)
            parts.append(f"[지난 상호작용]\n{note_lines}")
        return "\n\n".join(parts)
