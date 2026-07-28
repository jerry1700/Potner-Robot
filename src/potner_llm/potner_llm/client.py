from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Optional

from .prompts import SYSTEM_PLANT
from .tools import TOOL_DEFINITIONS, ToolHub

GMS_OPENAI_BASE_URL = "https://gms.ssafy.io/gmsapi/api.openai.com/v1"


@dataclass
class ChatMessage:
    role: str
    content: Optional[str] = None
    tool_calls: Optional[list[dict[str, Any]]] = None
    tool_call_id: Optional[str] = None
    name: Optional[str] = None

    def to_api(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"role": self.role}
        if self.content is not None:
            payload["content"] = self.content
        if self.tool_calls is not None:
            payload["tool_calls"] = self.tool_calls
        if self.tool_call_id is not None:
            payload["tool_call_id"] = self.tool_call_id
        if self.name is not None:
            payload["name"] = self.name
        return payload


@dataclass
class LlmClient:
    """OpenAI-compatible Chat Completions (+ tool use). SSAFY GMS 지원."""

    provider: str
    model: str
    api_key: Optional[str]
    base_url: str
    enabled: bool = True
    max_tokens: int = 256

    def available(self) -> bool:
        if not self.enabled:
            return False
        if self.provider == "mock":
            return False
        return bool(self.api_key)

    def complete(
        self,
        user_prompt: str,
        *,
        system: str = SYSTEM_PLANT,
        temperature: float = 0.7,
    ) -> Optional[str]:
        if not self.available():
            return None
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user_prompt},
        ]
        data = self._post_chat(messages, tools=None, temperature=temperature)
        return _assistant_text(data)

    def chat_with_tools(
        self,
        history: list[ChatMessage],
        tools: ToolHub,
        *,
        system: str = SYSTEM_PLANT,
        max_tool_rounds: int = 3,
        temperature: float = 0.7,
    ) -> str:
        if not self.available():
            raise RuntimeError("LLM unavailable - use template/offline chat fallback")

        messages: list[dict[str, Any]] = [{"role": "system", "content": system}]
        messages.extend(m.to_api() for m in history)

        for _ in range(max_tool_rounds + 1):
            data = self._post_chat(
                messages,
                tools=TOOL_DEFINITIONS,
                temperature=temperature,
            )
            choice = (data.get("choices") or [{}])[0]
            message = choice.get("message") or {}
            tool_calls = message.get("tool_calls") or []

            if not tool_calls:
                return str(message.get("content") or "").strip()

            messages.append(message)
            for call in tool_calls:
                fn = call.get("function") or {}
                name = fn.get("name") or ""
                raw_args = fn.get("arguments") or "{}"
                try:
                    args = json.loads(raw_args) if isinstance(raw_args, str) else (raw_args or {})
                except json.JSONDecodeError:
                    args = {}
                result = tools.call(name, args)
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.get("id"),
                        "content": json.dumps(result, ensure_ascii=False),
                    }
                )

        return "지금은 생각이 길어져서, 나중에 다시 물어봐 줄래?"

    def _post_chat(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: Optional[list[dict[str, Any]]],
        temperature: float,
    ) -> dict[str, Any]:
        url = self.base_url.rstrip("/") + "/chat/completions"
        body: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": self.max_tokens,
        }
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"

        req = urllib.request.Request(
            url,
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"LLM HTTP {exc.code}: {detail}") from exc


def create_llm_client(config: dict[str, Any]) -> LlmClient:
    llm = config.get("llm", {}) or {}
    provider = str(llm.get("provider", "gms")).lower()

    key_env = str(llm.get("api_key_env", "OPENAI_API_KEY"))
    raw_key = (
        os.environ.get(key_env)
        or os.environ.get("GMS_API_KEY")
        or llm.get("api_key")
    )
    api_key = str(raw_key).strip() if raw_key else None
    if api_key == "":
        api_key = None

    default_base = (
        GMS_OPENAI_BASE_URL if provider in {"gms", "ssafy"} else "https://api.openai.com/v1"
    )
    # 테스트/저비용 기본: nano. gpt-5-nano 도 .env 로 바꿀 수 있음
    default_model = "gpt-4.1-nano" if provider in {"gms", "ssafy"} else "gpt-4o-mini"

    model = os.environ.get("OPENAI_MODEL") or str(llm.get("model", default_model))
    base_url = os.environ.get("OPENAI_BASE_URL") or str(llm.get("base_url", default_base))
    max_tokens = int(os.environ.get("OPENAI_MAX_TOKENS") or llm.get("max_tokens", 256))

    return LlmClient(
        provider=provider,
        model=model,
        api_key=api_key,
        base_url=base_url,
        enabled=bool(llm.get("enabled", True)),
        max_tokens=max_tokens,
    )


def describe_llm(client: LlmClient, config: dict[str, Any]) -> str:
    llm = config.get("llm", {}) or {}
    key_env = str(llm.get("api_key_env", "OPENAI_API_KEY"))
    if not client.enabled:
        return "LLM=off (config llm.enabled=false)"
    if client.provider == "mock":
        return "LLM=mock (template fallback)"
    if not client.api_key:
        return f"LLM=off (no key) - put {key_env}=... or GMS_API_KEY=... in .env"
    masked = (
        client.api_key[:7] + "..." + client.api_key[-4:]
        if len(client.api_key) > 12
        else "***"
    )
    return (
        f"LLM=on provider={client.provider} model={client.model} "
        f"max_tokens={client.max_tokens} key={masked}\n"
        f"base_url={client.base_url}"
    )


def _assistant_text(data: dict[str, Any]) -> str:
    choice = (data.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    return str(message.get("content") or "").strip()
