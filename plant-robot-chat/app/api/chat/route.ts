/**
 * POST /api/chat — 초록이 대화 스트리밍 엔드포인트.
 *
 * 모델 호출은 예외 없이 스트리밍이다: GMS 게이트웨이가 비-스트리밍 응답 바디의
 * 앞부분을 잘라먹기 때문(실측). temperature/top_p/top_k 는 opus-4-7/4-8에서 400이라
 * 아예 넣지 않고, 톤은 시스템 프롬프트로만 잡는다.
 */

import Anthropic from '@anthropic-ai/sdk';

import { getClient } from '@/lib/anthropic';
import {
  MAX_INPUT_CHARS,
  MAX_TOKENS,
  MAX_TOOL_ROUNDS,
  RECENT_MESSAGE_WINDOW,
  getServerConfig,
  isAllowedOrigin,
} from '@/lib/config';
import { checkRateLimit } from '@/lib/rate-limit';
import { checkInput, filterOutput } from '@/lib/safety-filter';
import { getSensorReading } from '@/lib/sensor-store';
import { buildSystemPrompt } from '@/lib/system-prompt';
import { nearestSupportedCity, runTool, type ToolContext } from '@/lib/tool-handlers';
import { TOOLS, TOOL_STATUS } from '@/lib/tools';
import type {
  ChatRequestBody,
  ChatStreamEvent,
  ClientLocation,
  TokenUsage,
  ToolInvocation,
} from '@/types/chat';

export const runtime = 'nodejs';

// ------------------------------------------------------------- 사용자 문구
const MSG_FORBIDDEN = '요청 출처를 확인할 수 없어요.';
const MSG_BAD_BODY = '요청 형식이 올바르지 않아요.';
const MSG_EMPTY = '무슨 이야기를 할까요? 메시지를 입력해주세요 🌱';
const MSG_TOO_LONG = `메시지가 너무 길어요. ${MAX_INPUT_CHARS}자 이내로 줄여줄래요?`;
const MSG_RATE_LIMITED = '잠깐 쉬고 올게요. 조금 후에 다시 말해줘요 🌙';
const MSG_CONFIG_ERROR = '서버 설정 오류가 있어요. 관리자에게 알려주세요.';
const MSG_CONNECTION_ERROR = '연결이 잠시 끊겼어요. 조금 후에 다시 말해줄래요? 🌿';
const MSG_GENERIC_ERROR = '잠시 문제가 생겼어요. 조금 뒤에 다시 시도해주세요.';
const MSG_SAFETY_FALLBACK =
  '그 이야기는 제가 도와주기 어려워요. 대신 식물 이야기를 해볼까요? 🌱';
const MSG_TOOL_LIMIT =
  '\n\n미안해요, 알아보는 데 시간이 너무 걸렸어요. 조금 더 구체적으로 다시 물어봐 줄래요? 🌱';
const DEFAULT_TOOL_STATUS = '🌱 잠시 알아보고 있어요...';

const SSE_HEADERS: Record<string, string> = {
  'Content-Type': 'text/event-stream; charset=utf-8',
  'Cache-Control': 'no-cache, no-transform',
  Connection: 'keep-alive',
  // 프록시(nginx 등)가 SSE를 버퍼링하면 델타가 뭉쳐서 도착한다.
  'X-Accel-Buffering': 'no',
};

// ------------------------------------------------------------- 작은 도우미

/** SSE 와이어 포맷: `event: <type>\ndata: <json>\n\n` */
function sseFrame(event: ChatStreamEvent): string {
  return `event: ${event.type}\ndata: ${JSON.stringify(event)}\n\n`;
}

/** 스트림을 열기 전 단계의 실패. 본문 모양을 error 이벤트와 맞춰 클라이언트 분기를 단순화한다. */
function jsonError(
  status: number,
  message: string,
  extraHeaders?: Record<string, string>,
): Response {
  return new Response(JSON.stringify({ type: 'error', message }), {
    status,
    headers: {
      'Content-Type': 'application/json; charset=utf-8',
      ...extraHeaders,
    },
  });
}

function asRecord(value: unknown): Record<string, unknown> | null {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return null;
  return value as Record<string, unknown>;
}

/** 클라이언트가 보낸 위치는 신뢰하지 않는다 — 범위를 벗어난 좌표는 조용히 버린다. */
function parseClientLocation(raw: unknown): ClientLocation | undefined {
  const record = asRecord(raw);
  if (!record) return undefined;

  const { latitude, longitude } = record;
  if (typeof latitude !== 'number' || typeof longitude !== 'number') return undefined;
  if (!Number.isFinite(latitude) || !Number.isFinite(longitude)) return undefined;
  if (latitude < -90 || latitude > 90 || longitude < -180 || longitude > 180) return undefined;

  return { latitude, longitude };
}

/** 도구 핸들러는 실패를 throw하지 않고 `{ error }` 객체로 돌려주기로 계약했다. */
function isToolError(value: unknown): boolean {
  const record = asRecord(value);
  return record !== null && record.error !== undefined;
}

/** tool_result 블록과 UI 카드가 같은 문자열을 쓰도록 직렬화를 한 곳에서 한다. */
function serializeToolResult(value: unknown): string {
  try {
    return JSON.stringify(value ?? null);
  } catch {
    // 순환 참조 등 직렬화 불가 — 모델에게도 실패로 알려야 한다.
    return '{"error":"도구 결과를 읽지 못했어요"}';
  }
}

/** 사용자가 중단(■)하거나 탭을 닫으면 SDK가 abort 에러를 던진다 — 에러 이벤트를 보낼 이유가 없다. */
function isAbort(error: unknown): boolean {
  return error instanceof Error && error.name === 'APIUserAbortError';
}

/** getServerConfig() 는 키가 없을 때 환경변수 이름을 담은 평범한 Error를 던진다. */
function isMissingKeyError(error: unknown): boolean {
  return (
    error instanceof Error && /ANTHROPIC_API_KEY|GMS_API_KEY/.test(error.message)
  );
}

/** API 키나 스택트레이스가 클라이언트로 새지 않도록, 고정된 한국어 문구로만 변환한다. */
function toUserFacingError(error: unknown): string {
  if (error instanceof Anthropic.RateLimitError) return MSG_RATE_LIMITED;
  if (error instanceof Anthropic.AuthenticationError) return MSG_CONFIG_ERROR;
  if (error instanceof Anthropic.APIConnectionError) return MSG_CONNECTION_ERROR;
  if (isMissingKeyError(error)) return MSG_CONFIG_ERROR;
  return MSG_GENERIC_ERROR;
}

/** 클라이언트가 보낸 히스토리는 신뢰할 수 없으므로 역할/내용을 직접 검증한다. */
function toMessageParams(history: unknown): Anthropic.MessageParam[] {
  if (!Array.isArray(history)) return [];

  const messages: Anthropic.MessageParam[] = [];
  for (const raw of history as unknown[]) {
    const item = asRecord(raw);
    if (item === null) continue;
    if (typeof item.content !== 'string') continue;

    // 빈 content 블록은 API가 400으로 거부한다.
    const content = item.content.trim();
    if (content.length === 0) continue;

    if (item.role === 'user' || item.role === 'assistant') {
      messages.push({ role: item.role, content });
    }
  }

  // 오래된 턴은 요약(summary)이 대신하므로 최근 창만 프롬프트에 넣는다.
  const recent = messages.slice(-RECENT_MESSAGE_WINDOW);
  // 첫 메시지가 assistant면 400이다 — 잘라낸 자리에서 생길 수 있어 여기서 막는다.
  let start = 0;
  while (start < recent.length && recent[start].role !== 'user') start += 1;
  return recent.slice(start);
}

/** 필터 결과를 이미 보낸 텍스트에 이어붙일 지점을 찾는다. */
function commonPrefixLength(a: string, b: string): number {
  const limit = Math.min(a.length, b.length);
  let i = 0;
  while (i < limit && a.charCodeAt(i) === b.charCodeAt(i)) i += 1;

  // 서로게이트 페어 중간에서 자르면 이모지가 깨진 채로 전송된다.
  if (i > 0) {
    const lead = a.charCodeAt(i - 1);
    if (lead >= 0xd800 && lead <= 0xdbff) i -= 1;
  }
  return i;
}

/** 안전 필터 차단처럼 모델 호출 없이 끝나는 경우도 UI가 같은 경로로 렌더하도록 SSE로 응답한다. */
function sseResponseOf(events: ChatStreamEvent[]): Response {
  const encoder = new TextEncoder();
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const event of events) {
        controller.enqueue(encoder.encode(sseFrame(event)));
      }
      controller.close();
    },
  });
  return new Response(stream, { status: 200, headers: SSE_HEADERS });
}

// ------------------------------------------------------------------ 핸들러

export async function POST(req: Request): Promise<Response> {
  if (!isAllowedOrigin(req)) {
    return jsonError(403, MSG_FORBIDDEN);
  }

  let body: ChatRequestBody;
  try {
    body = (await req.json()) as ChatRequestBody;
  } catch {
    return jsonError(400, MSG_BAD_BODY);
  }

  const message = typeof body.message === 'string' ? body.message.trim() : '';
  if (message.length === 0) {
    return jsonError(400, MSG_EMPTY);
  }
  if (message.length > MAX_INPUT_CHARS) {
    return jsonError(400, MSG_TOO_LONG);
  }

  const rate = checkRateLimit(body.conversationId ?? 'anonymous');
  if (!rate.allowed) {
    const retryAfter = rate.retryAfterSeconds;
    return jsonError(
      429,
      MSG_RATE_LIMITED,
      retryAfter === undefined ? undefined : { 'Retry-After': String(retryAfter) },
    );
  }

  // 안전 필터에 걸리면 모델을 호출하지 않는다. 그래도 200 SSE로 돌려줘서
  // 클라이언트가 평소 답변과 똑같이 렌더하게 한다.
  const safety = checkInput(message);
  if (!safety.safe) {
    return sseResponseOf([
      { type: 'text_delta', text: safety.fallbackResponse ?? MSG_SAFETY_FALLBACK },
      {
        type: 'message_complete',
        usage: { input_tokens: 0, output_tokens: 0 },
        toolUse: [],
      },
    ]);
  }

  // 키가 없으면 스트림을 열기 전에 JSON 500으로 끝낸다(열린 뒤엔 상태 코드를 못 바꾼다).
  let model: string;
  let client: Anthropic;
  try {
    model = getServerConfig().model;
    client = getClient();
  } catch (error) {
    return jsonError(500, toUserFacingError(error));
  }

  const now = new Date();
  const location = parseClientLocation(body.location);
  const toolContext: ToolContext = { sensorReading: getSensorReading(), location, now };

  const system = buildSystemPrompt(body.summary, {
    now,
    nearestCityLabel: location ? nearestSupportedCity(location).label : undefined,
  });
  const messages: Anthropic.MessageParam[] = [
    ...toMessageParams(body.history),
    // 프리필 금지: 마지막 메시지는 항상 user 턴이다.
    { role: 'user', content: message },
  ];

  const encoder = new TextEncoder();
  let clientGone = false;

  const stream = new ReadableStream<Uint8Array>({
    async start(controller) {
      const send = (event: ChatStreamEvent): void => {
        if (clientGone) return;
        try {
          controller.enqueue(encoder.encode(sseFrame(event)));
        } catch {
          // 사용자가 이미 연결을 끊은 경우(cancel 콜백보다 먼저 도달할 수 있다).
          clientGone = true;
        }
      };

      /** 클라이언트가 렌더할 텍스트와 정확히 같은 누적본 (delta로 보낸 것만 담는다). */
      let assistantText = '';
      const toolUse: ToolInvocation[] = [];
      // 라운드마다 프롬프트가 다시 계산되므로 input_tokens는 누적 합계다.
      const usage: TokenUsage = { input_tokens: 0, output_tokens: 0 };
      let hitToolLimit = false;

      try {
        for (let round = 1; ; round += 1) {
          const modelStream = client.messages.stream(
            {
              model,
              max_tokens: MAX_TOKENS,
              system,
              messages,
              tools: TOOLS,
            },
            { signal: req.signal },
          );

          for await (const event of modelStream) {
            if (event.type !== 'content_block_delta') continue;
            if (event.delta.type !== 'text_delta') continue;
            assistantText += event.delta.text;
            send({ type: 'text_delta', text: event.delta.text });
          }

          const final = await modelStream.finalMessage();
          usage.input_tokens += final.usage.input_tokens;
          usage.output_tokens += final.usage.output_tokens;

          if (final.stop_reason !== 'tool_use') break;

          if (round >= MAX_TOOL_ROUNDS) {
            // 도구를 더 부르려 하지만 상한이다 — 사과만 붙이고 깔끔하게 끝낸다.
            hitToolLimit = true;
            break;
          }

          // 이번 턴의 assistant 블록을 그대로 되돌려줘야 tool_use_id가 매칭된다.
          // 응답 블록을 통째로 재사용하지 않고 필요한 필드만 재구성한다
          // (thinking 미사용 + citations 등 응답/요청 타입 불일치 회피).
          const assistantBlocks: Array<
            Anthropic.TextBlockParam | Anthropic.ToolUseBlockParam
          > = [];
          const toolResults: Anthropic.ToolResultBlockParam[] = [];

          for (const block of final.content) {
            if (block.type === 'text') {
              assistantBlocks.push({ type: 'text', text: block.text });
              continue;
            }
            if (block.type !== 'tool_use') continue;

            assistantBlocks.push({
              type: 'tool_use',
              id: block.id,
              name: block.name,
              input: block.input,
            });

            const status = TOOL_STATUS[block.name] ?? DEFAULT_TOOL_STATUS;
            send({ type: 'tool_use_start', tool: block.name, status });

            // 순차 실행: 상태 문구가 사용자에게 호출 순서대로 보이게 한다.
            const result = await runTool(block.name, asRecord(block.input) ?? {}, toolContext);
            const isError = isToolError(result);
            const serialized = serializeToolResult(result);

            send({ type: 'tool_use_end', tool: block.name, status, isError });

            toolUse.push({
              id: block.id,
              name: block.name,
              input: block.input,
              result: serialized,
              isError,
            });

            const resultBlock: Anthropic.ToolResultBlockParam = {
              type: 'tool_result',
              tool_use_id: block.id,
              content: serialized,
            };
            if (isError) resultBlock.is_error = true;
            toolResults.push(resultBlock);
          }

          messages.push({ role: 'assistant', content: assistantBlocks });
          // 한 턴의 tool_result는 반드시 하나의 user 메시지에 모아 보낸다.
          // 쪼개 보내면 모델이 병렬 도구 호출을 그만두도록 학습된다.
          messages.push({ role: 'user', content: toolResults });
        }

        if (hitToolLimit) {
          assistantText += MSG_TOOL_LIMIT;
          send({ type: 'text_delta', text: MSG_TOOL_LIMIT });
        }

        // 클라이언트는 받은 delta를 이어붙여 렌더한다. 즉 이미 보낸 텍스트는 회수할 수
        // 없고, 서버·클라이언트 텍스트를 맞추는 수단은 "차이를 뒤에 덧붙이기"뿐이다.
        //  - 면책 문구 추가처럼 접두사가 유지되면: 늘어난 뒷부분만 보내 완전히 일치한다.
        //  - 길이 초과로 잘린 경우: 공통 접두사 뒤의 꼬리(말줄임/안내)만 보낸다.
        //    이 경우에만 클라이언트 텍스트가 필터 결과보다 길게 남는다(회수 불가).
        const filtered = filterOutput(assistantText);
        if (filtered !== assistantText) {
          const from = filtered.startsWith(assistantText)
            ? assistantText.length
            : commonPrefixLength(assistantText, filtered);
          const suffix = filtered.slice(from);
          if (suffix.length > 0) send({ type: 'text_delta', text: suffix });
        }

        send({ type: 'message_complete', usage, toolUse });
      } catch (error) {
        // 스트림이 이미 열려 있어 상태 코드로는 알릴 수 없다 → error 이벤트로 보낸다.
        if (!req.signal.aborted && !isAbort(error)) {
          send({ type: 'error', message: toUserFacingError(error) });
        }
      } finally {
        try {
          if (!clientGone) controller.close();
        } catch {
          // 이미 닫힌 스트림 — 무시.
        }
      }
    },

    cancel() {
      // 사용자가 중단(■)하거나 탭을 닫은 경우. 닫힌 컨트롤러에 enqueue하면 throw된다.
      clientGone = true;
    },
  });

  return new Response(stream, { status: 200, headers: SSE_HEADERS });
}
