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

import requests

from .exceptions import (
    LlmApiError,
    LlmAuthError,
    LlmConnectionError,
    LlmRateLimitError,
    LlmResponseParseError,
    LlmTimeoutError,
)
from .prompts import SYSTEM_PLANT
from .tools import TOOL_DEFINITIONS, ToolHub, anthropic_tool_definitions

GMS_OPENAI_BASE_URL = "https://gms.ssafy.io/gmsapi/api.openai.com/v1"
GMS_ANTHROPIC_BASE_URL = "https://gms.ssafy.io/gmsapi/api.anthropic.com"
ANTHROPIC_VERSION = "2023-06-01"

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
                        f"LLM HTTP {exc.code}, retrying in {delay:.1f}s "
                        f"(attempt {attempt + 1}/{self.max_retries}): {detail}"
                    )
                    time.sleep(delay)
                    attempt += 1
                    continue
                logger.error(f"LLM HTTP {exc.code} (giving up): {detail}")
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
                    kind = "timeout" if is_timeout else "connection error"
                    logger.warning(
                        f"LLM {kind}, retrying in {delay:.1f}s "
                        f"(attempt {attempt + 1}/{self.max_retries}): {exc.reason}"
                    )
                    time.sleep(delay)
                    attempt += 1
                    continue
                logger.error(f"LLM connection error (giving up): {exc.reason}")
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
                        f"LLM read timeout, retrying in {delay:.1f}s "
                        f"(attempt {attempt + 1}/{self.max_retries})"
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
                        f"LLM 응답 JSON 파싱 실패, {delay:.1f}s 후 재시도 "
                        f"(attempt {attempt + 1}/{self.max_retries}): {exc} "
                        f"| body[:200]={raw_text[:200]!r}"
                    )
                    time.sleep(delay)
                    attempt += 1
                    continue
                logger.error(
                    f"LLM 응답 JSON 파싱 실패 (giving up): {exc} | body[:200]={raw_text[:200]!r}"
                )
                raise LlmResponseParseError(f"LLM response JSON parse error: {exc}") from exc

            usage = data.get("usage")
            if usage:
                logger.info(f"LLM call ok model={self.model} usage={usage} (attempt {attempt + 1})")
            return data


class AnthropicLlmClient(LlmClient):
    """Anthropic Messages API (+ tool use), GMS Anthropic 프록시 경유.

    GMS 프록시 실측 제약 (plant-robot-chat에서 검증, docs 참고):
    - 비스트리밍 응답은 바디 앞부분이 유실된다 → 모든 호출을 SSE 스트리밍으로 받는다.
    - temperature/top_p/top_k를 보내면 400 → 보내지 않는다 (인자는 호환용으로 받고 무시).
    - 인증은 x-api-key 헤더 (Authorization: Bearer는 401).
    - 스트리밍은 stdlib urllib로는 곤란해서 requests를 쓴다 (speech.py와 동일 결정).
    """

    def complete(
        self,
        user_prompt: str,
        *,
        system: str = SYSTEM_PLANT,
        temperature: float = 0.7,
    ) -> Optional[str]:
        del temperature  # GMS Anthropic 프록시는 temperature를 거부한다(400)
        if not self.available():
            return None
        result = self._post_messages(
            [{"role": "user", "content": user_prompt}], system=system, tools=None
        )
        return result["text"].strip()

    def chat_with_tools(
        self,
        history: list[ChatMessage],
        tools: ToolHub,
        *,
        system: str = SYSTEM_PLANT,
        max_tool_rounds: int = 3,
        temperature: float = 0.7,
    ) -> str:
        del temperature  # GMS Anthropic 프록시는 temperature를 거부한다(400)
        if not self.available():
            raise RuntimeError("LLM unavailable - use template/offline chat fallback")

        messages = _to_anthropic_messages(history)
        tool_defs = anthropic_tool_definitions()

        for _ in range(max_tool_rounds + 1):
            result = self._post_messages(messages, system=system, tools=tool_defs)
            if result["stop_reason"] != "tool_use" or not result["tool_uses"]:
                return result["text"].strip()

            messages.append({"role": "assistant", "content": result["content"]})
            result_blocks = []
            for use in result["tool_uses"]:
                out = tools.call(use["name"], use["input"])
                result_blocks.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": use["id"],
                        "content": json.dumps(out, ensure_ascii=False),
                    }
                )
            messages.append({"role": "user", "content": result_blocks})

        return "지금은 생각이 길어져서, 나중에 다시 물어봐 줄래?"

    def _post_messages(
        self,
        messages: list[dict[str, Any]],
        *,
        system: str,
        tools: Optional[list[dict[str, Any]]],
    ) -> dict[str, Any]:
        url = self.base_url.rstrip("/") + "/v1/messages"
        body: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "system": system,
            "messages": messages,
            "stream": True,
        }
        if tools:
            body["tools"] = tools
        headers = {
            "Content-Type": "application/json",
            "x-api-key": self.api_key,
            "anthropic-version": ANTHROPIC_VERSION,
        }

        attempt = 0
        while True:
            try:
                resp = requests.post(
                    url,
                    json=body,
                    headers=headers,
                    stream=True,
                    timeout=self.request_timeout_seconds,
                )
            except requests.exceptions.Timeout as exc:
                if attempt < self.max_retries:
                    attempt = self._wait_retry(attempt, f"timeout: {exc}")
                    continue
                logger.error("LLM(Anthropic) timeout (giving up)")
                raise LlmTimeoutError(
                    f"LLM timeout after {self.request_timeout_seconds}s"
                ) from exc
            except requests.exceptions.RequestException as exc:
                if attempt < self.max_retries:
                    attempt = self._wait_retry(attempt, f"connection error: {exc}")
                    continue
                logger.error(f"LLM(Anthropic) connection error (giving up): {exc}")
                raise LlmConnectionError(f"LLM connection error: {exc}") from exc

            try:
                if resp.status_code != 200:
                    detail = resp.text[:300]
                    if resp.status_code in RETRYABLE_HTTP_STATUS and attempt < self.max_retries:
                        attempt = self._wait_retry(attempt, f"HTTP {resp.status_code}")
                        continue
                    logger.error(f"LLM(Anthropic) HTTP {resp.status_code} (giving up): {detail}")
                    if resp.status_code in {401, 403}:
                        raise LlmAuthError(
                            f"LLM auth failed (HTTP {resp.status_code}): {detail}"
                        )
                    if resp.status_code == 429:
                        raise LlmRateLimitError(f"LLM rate limited: {detail}")
                    raise LlmApiError(resp.status_code, detail)
                return self._consume_stream(resp)
            except requests.exceptions.Timeout as exc:
                if attempt < self.max_retries:
                    attempt = self._wait_retry(attempt, f"read timeout: {exc}")
                    continue
                logger.error("LLM(Anthropic) read timeout (giving up)")
                raise LlmTimeoutError(
                    f"LLM timeout after {self.request_timeout_seconds}s"
                ) from exc
            except requests.exceptions.RequestException as exc:
                # 스트림 소비 중 끊김(ChunkedEncodingError 등) — 전체 호출을 재시도한다.
                if attempt < self.max_retries:
                    attempt = self._wait_retry(attempt, f"stream error: {exc}")
                    continue
                logger.error(f"LLM(Anthropic) stream error (giving up): {exc}")
                raise LlmConnectionError(f"LLM stream error: {exc}") from exc
            finally:
                resp.close()

    def _wait_retry(self, attempt: int, reason: str) -> int:
        delay = self.retry_backoff_seconds * (2**attempt)
        logger.warning(
            f"LLM(Anthropic) {reason}, {delay:.1f}s 후 재시도 "
            f"(attempt {attempt + 1}/{self.max_retries})"
        )
        time.sleep(delay)
        return attempt + 1

    def _consume_stream(self, resp: Any) -> dict[str, Any]:
        """SSE 이벤트를 끝까지 모아 비스트리밍처럼 하나의 결과로 돌려준다.

        반환: {"text": 전체 텍스트, "content": 원본 블록 목록(대화 이력용),
              "tool_uses": tool_use 블록만, "stop_reason": ...}
        """
        text_parts: list[str] = []
        content: list[dict[str, Any]] = []
        tool_uses: list[dict[str, Any]] = []
        stop_reason: Optional[str] = None
        usage: dict[str, Any] = {}
        current: Optional[dict[str, Any]] = None
        event = ""

        for line in resp.iter_lines(decode_unicode=True):
            if not line:
                continue
            if line.startswith("event:"):
                event = line[len("event:"):].strip()
                continue
            if not line.startswith("data:"):
                continue
            try:
                data = json.loads(line[len("data:"):].strip() or "{}")
            except ValueError:
                continue

            if event == "message_start":
                usage.update((data.get("message") or {}).get("usage") or {})
            elif event == "content_block_start":
                block = data.get("content_block") or {}
                if block.get("type") == "tool_use":
                    current = {
                        "type": "tool_use",
                        "id": block.get("id"),
                        "name": block.get("name"),
                        "partial": [],
                    }
                elif block.get("type") == "text":
                    current = {"type": "text", "parts": []}
                else:
                    current = None
            elif event == "content_block_delta":
                delta = data.get("delta") or {}
                if current is None:
                    continue
                if delta.get("type") == "text_delta" and current["type"] == "text":
                    current["parts"].append(delta.get("text", ""))
                elif delta.get("type") == "input_json_delta" and current["type"] == "tool_use":
                    current["partial"].append(delta.get("partial_json", ""))
            elif event == "content_block_stop":
                if current is None:
                    continue
                if current["type"] == "text":
                    text = "".join(current["parts"])
                    text_parts.append(text)
                    content.append({"type": "text", "text": text})
                else:
                    raw = "".join(current["partial"]) or "{}"
                    try:
                        args = json.loads(raw)
                    except ValueError:
                        args = {}
                    use = {
                        "type": "tool_use",
                        "id": current["id"],
                        "name": current["name"],
                        "input": args,
                    }
                    content.append(use)
                    tool_uses.append(use)
                current = None
            elif event == "message_delta":
                stop_reason = (data.get("delta") or {}).get("stop_reason") or stop_reason
                usage.update(data.get("usage") or {})
            elif event == "error":
                err = data.get("error") or {}
                raise LlmApiError(500, str(err.get("message") or err))
            elif event == "message_stop":
                break

        if usage:
            logger.info(f"LLM call ok model={self.model} usage={usage}")
        return {
            "text": "".join(text_parts),
            "content": content,
            "tool_uses": tool_uses,
            "stop_reason": stop_reason,
        }


def _to_anthropic_messages(history: list[ChatMessage]) -> list[dict[str, Any]]:
    """OpenAI 형식 ChatMessage 이력을 Anthropic Messages 형식으로 변환한다.

    ChatMessage/context_builder는 OpenAI 형식 그대로 두고 여기서만 변환한다 —
    provider를 되돌릴 때(config 1줄) 이력 형식이 함께 흔들리지 않게.

    - assistant의 tool_calls → tool_use 블록
    - role="tool" 응답 → 직후 user 메시지의 tool_result 블록 (연속이면 하나로 합침)
    """
    messages: list[dict[str, Any]] = []
    for m in history:
        if m.role == "tool":
            block = {
                "type": "tool_result",
                "tool_use_id": m.tool_call_id,
                "content": m.content or "",
            }
            last = messages[-1] if messages else None
            if last and last["role"] == "user" and isinstance(last["content"], list):
                last["content"].append(block)
            else:
                messages.append({"role": "user", "content": [block]})
            continue
        if m.role == "assistant" and m.tool_calls:
            blocks: list[dict[str, Any]] = []
            if m.content:
                blocks.append({"type": "text", "text": m.content})
            for call in m.tool_calls:
                fn = call.get("function") or {}
                raw_args = fn.get("arguments") or "{}"
                try:
                    args = json.loads(raw_args) if isinstance(raw_args, str) else (raw_args or {})
                except json.JSONDecodeError:
                    args = {}
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": call.get("id"),
                        "name": fn.get("name"),
                        "input": args,
                    }
                )
            messages.append({"role": "assistant", "content": blocks})
            continue
        messages.append({"role": m.role, "content": m.content or ""})
    return messages


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

    if provider == "anthropic":
        # GMS Anthropic 프록시 (allowlist: opus-4-8/4-7/4-6, sonnet-4-6).
        # OPENAI_MODEL 등 기존 env가 남아 있어도 오염되지 않게 ANTHROPIC_* 만 읽는다.
        model = os.environ.get("ANTHROPIC_MODEL") or str(llm.get("model", "claude-opus-4-8"))
        base_url = os.environ.get("ANTHROPIC_BASE_URL") or str(
            llm.get("base_url", GMS_ANTHROPIC_BASE_URL)
        )
    else:
        default_base = (
            GMS_OPENAI_BASE_URL if provider in {"gms", "ssafy"} else "https://api.openai.com/v1"
        )
        # 테스트/저비용 기본: nano. gpt-5-nano 도 .env 로 바꿀 수 있음
        default_model = "gpt-4.1-nano" if provider in {"gms", "ssafy"} else "gpt-4o-mini"
        model = os.environ.get("OPENAI_MODEL") or str(llm.get("model", default_model))
        base_url = os.environ.get("OPENAI_BASE_URL") or str(llm.get("base_url", default_base))
    # max_tokens도 provider별 env를 읽는다 — .env에 남은 OPENAI_MAX_TOKENS(=200)가
    # anthropic 쪽 한글 답변을 자르는 사고를 막는다.
    max_tokens_env = "ANTHROPIC_MAX_TOKENS" if provider == "anthropic" else "OPENAI_MAX_TOKENS"
    max_tokens = int(os.environ.get(max_tokens_env) or llm.get("max_tokens", 256))
    max_retries = int(os.environ.get("OPENAI_MAX_RETRIES") or llm.get("max_retries", 2))
    retry_backoff_seconds = float(
        os.environ.get("OPENAI_RETRY_BACKOFF_SECONDS") or llm.get("retry_backoff_seconds", 0.5)
    )
    request_timeout_seconds = float(
        os.environ.get("OPENAI_TIMEOUT_SECONDS") or llm.get("request_timeout_seconds", 60.0)
    )

    client_cls = AnthropicLlmClient if provider == "anthropic" else LlmClient
    return client_cls(
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
