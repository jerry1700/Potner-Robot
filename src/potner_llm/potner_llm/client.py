from __future__ import annotations

import json
import logging
import os
import socket
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Optional

from .exceptions import (
    LlmApiError,
    LlmAuthError,
    LlmConnectionError,
    LlmRateLimitError,
    LlmResponseParseError,
    LlmTimeoutError,
)
from .prompts import SYSTEM_PLANT
from .tools import TOOL_DEFINITIONS, ToolHub

GMS_OPENAI_BASE_URL = "https://gms.ssafy.io/gmsapi/api.openai.com/v1"

# 429(rate limit)/5xx(서버 오류)는 재시도하면 성공할 가능성이 있는 오류.
# 401/403(인증) 등은 재시도해도 결과가 같으므로 제외.
RETRYABLE_HTTP_STATUS = {429, 500, 502, 503, 504}

logger = logging.getLogger(__name__)


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
    max_retries: int = 2
    retry_backoff_seconds: float = 0.5
    request_timeout_seconds: float = 60.0

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

        req_body = json.dumps(body).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

        attempt = 0
        while True:
            req = urllib.request.Request(url, data=req_body, headers=headers, method="POST")
            try:
                with urllib.request.urlopen(req, timeout=self.request_timeout_seconds) as resp:
                    raw_text = resp.read().decode("utf-8")
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")
                if attempt < self.max_retries and exc.code in RETRYABLE_HTTP_STATUS:
                    delay = self.retry_backoff_seconds * (2**attempt)
                    logger.warning(
                        "LLM HTTP %d, retrying in %.1fs (attempt %d/%d): %s",
                        exc.code,
                        delay,
                        attempt + 1,
                        self.max_retries,
                        detail,
                    )
                    time.sleep(delay)
                    attempt += 1
                    continue
                logger.error("LLM HTTP %d (giving up): %s", exc.code, detail)
                if exc.code in {401, 403}:
                    raise LlmAuthError(f"LLM auth failed (HTTP {exc.code}): {detail}") from exc
                if exc.code == 429:
                    raise LlmRateLimitError(f"LLM rate limited: {detail}") from exc
                raise LlmApiError(exc.code, detail) from exc
            except urllib.error.URLError as exc:
                # 연결 단계 timeout은 URLError(reason=timeout)로 온다.
                is_timeout = isinstance(exc.reason, (socket.timeout, TimeoutError))
                if attempt < self.max_retries:
                    delay = self.retry_backoff_seconds * (2**attempt)
                    logger.warning(
                        "LLM %s, retrying in %.1fs (attempt %d/%d): %s",
                        "timeout" if is_timeout else "connection error",
                        delay,
                        attempt + 1,
                        self.max_retries,
                        exc.reason,
                    )
                    time.sleep(delay)
                    attempt += 1
                    continue
                logger.error("LLM connection error (giving up): %s", exc.reason)
                if is_timeout:
                    raise LlmTimeoutError(
                        f"LLM timeout after {self.request_timeout_seconds}s"
                    ) from exc
                raise LlmConnectionError(f"LLM connection error: {exc.reason}") from exc
            except (socket.timeout, TimeoutError) as exc:
                # 응답 본문을 읽는 도중의 timeout은 wrap 없이 그대로 올라온다.
                if attempt < self.max_retries:
                    delay = self.retry_backoff_seconds * (2**attempt)
                    logger.warning(
                        "LLM read timeout, retrying in %.1fs (attempt %d/%d)",
                        delay,
                        attempt + 1,
                        self.max_retries,
                    )
                    time.sleep(delay)
                    attempt += 1
                    continue
                logger.error("LLM read timeout (giving up)")
                raise LlmTimeoutError(
                    f"LLM timeout after {self.request_timeout_seconds}s"
                ) from exc

            try:
                data = json.loads(raw_text)
            except json.JSONDecodeError as exc:
                # 게이트웨이가 아주 가끔 200 OK인데 응답 바디가 깨져서 오는 경우가
                # 있다(중간 프록시/네트워크 문제로 추정). HTTP 오류와 동일하게
                # 재시도 대상으로 취급한다 - 재전송하면 보통 정상 응답이 온다.
                if attempt < self.max_retries:
                    delay = self.retry_backoff_seconds * (2**attempt)
                    logger.warning(
                        "LLM 응답 JSON 파싱 실패, %.1fs 후 재시도 (attempt %d/%d): %s | body[:200]=%r",
                        delay,
                        attempt + 1,
                        self.max_retries,
                        exc,
                        raw_text[:200],
                    )
                    time.sleep(delay)
                    attempt += 1
                    continue
                logger.error(
                    "LLM 응답 JSON 파싱 실패 (giving up): %s | body[:200]=%r", exc, raw_text[:200]
                )
                raise LlmResponseParseError(f"LLM response JSON parse error: {exc}") from exc

            usage = data.get("usage")
            if usage:
                logger.info(
                    "LLM call ok model=%s usage=%s (attempt %d)", self.model, usage, attempt + 1
                )
            return data


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
    max_retries = int(os.environ.get("OPENAI_MAX_RETRIES") or llm.get("max_retries", 2))
    retry_backoff_seconds = float(
        os.environ.get("OPENAI_RETRY_BACKOFF_SECONDS") or llm.get("retry_backoff_seconds", 0.5)
    )
    request_timeout_seconds = float(
        os.environ.get("OPENAI_TIMEOUT_SECONDS") or llm.get("request_timeout_seconds", 60.0)
    )

    return LlmClient(
        provider=provider,
        model=model,
        api_key=api_key,
        base_url=base_url,
        enabled=bool(llm.get("enabled", True)),
        max_tokens=max_tokens,
        max_retries=max_retries,
        retry_backoff_seconds=retry_backoff_seconds,
        request_timeout_seconds=request_timeout_seconds,
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
