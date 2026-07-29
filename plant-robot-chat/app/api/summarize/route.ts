/**
 * POST /api/summarize — 오래된 대화를 압축한다.
 *
 * 요약은 초록이의 "기억"이므로 previousSummary를 접어 넣어 누적시킨다.
 * 실패해도 200 + 기존 요약을 그대로 돌려준다 (요약 실패로 대화가 끊기면 안 된다).
 */

import { streamText } from '@/lib/anthropic';
import { isAllowedOrigin } from '@/lib/config';
import type {
  ChatHistoryItem,
  SummarizeRequestBody,
  SummarizeResponseBody,
} from '@/types/chat';

export const runtime = 'nodejs';

const SUMMARY_MAX_TOKENS = 800;

/** 대화록이 너무 길면 컨텍스트를 넘기므로 최근 대화 위주로 자른다. */
const MAX_TRANSCRIPT_CHARS = 24_000;

/** 요약이 무한히 자라지 않게 하는 방어선 */
const MAX_SUMMARY_CHARS = 4_000;

const SUMMARY_SYSTEM = `너는 반려식물 상담 챗봇 '초록이'의 기억 정리 담당이다.
오래된 대화를 압축해서, 초록이가 앞으로도 기억해야 할 것만 남긴다.

반드시 보존할 항목:
1. 사용자가 키우는 식물 (이름, 별칭, 키운 기간, 화분 상태)
2. 환경 — 빛(창 방향/광량), 공간(집·사무실·베란다), 온도·습도 특징
3. 식물 경험 수준 (초보/중급 등)과 관리 습관(물주기 주기 등)
4. 반려동물 유무와 종류 (독성 식물 안내에 필요)
5. 진행 중인 문제와 이미 시도한 조치, 그 결과
6. 감정적 맥락 — 애착, 걱정, 상실, 선물받은 사연 등

작성 규칙:
- 한국어로, 항목별 짧은 불릿으로 쓴다.
- 기존 요약이 주어지면 덮어쓰지 말고 새 내용을 합쳐 갱신한다. 모순되면 최신 정보를 따른다.
- 인사말·잡담·중복은 버린다. 추측해서 만들어내지 않는다.
- 요약문만 출력한다. 서론이나 마무리 문장을 붙이지 않는다.`;

function jsonResponse(body: SummarizeResponseBody, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json; charset=utf-8' },
  });
}

function isHistoryItem(value: unknown): value is ChatHistoryItem {
  if (!value || typeof value !== 'object') return false;
  const item = value as Partial<ChatHistoryItem>;
  return (
    (item.role === 'user' || item.role === 'assistant') &&
    typeof item.content === 'string'
  );
}

function parseBody(raw: unknown): SummarizeRequestBody {
  if (!raw || typeof raw !== 'object') return { messages: [] };
  const body = raw as Partial<SummarizeRequestBody>;
  return {
    messages: Array.isArray(body.messages) ? body.messages.filter(isHistoryItem) : [],
    previousSummary:
      typeof body.previousSummary === 'string' ? body.previousSummary : undefined,
  };
}

/** 역할을 사람이 읽는 이름으로 바꿔, 모델이 화자를 헷갈리지 않게 한다. */
function toTranscript(messages: ChatHistoryItem[]): string {
  const lines = messages.map(
    (m) => `${m.role === 'user' ? '사용자' : '초록이'}: ${m.content.trim()}`,
  );
  const full = lines.join('\n');
  if (full.length <= MAX_TRANSCRIPT_CHARS) return full;

  // 넘칠 때는 앞부분을 버린다 — 더 오래된 내용은 previousSummary가 이미 담고 있다.
  return `(앞부분 생략)\n${full.slice(full.length - MAX_TRANSCRIPT_CHARS)}`;
}

export async function POST(req: Request): Promise<Response> {
  if (!isAllowedOrigin(req)) {
    return jsonResponse({ summary: '' }, 403);
  }

  const raw: unknown = await req.json().catch(() => null);
  const { messages, previousSummary } = parseBody(raw);
  const previous = previousSummary?.trim() ?? '';

  // 압축할 새 대화가 없으면 모델을 부를 이유가 없다.
  if (messages.length === 0) {
    return jsonResponse({ summary: previous });
  }

  try {
    const prompt = [
      previous ? `<기존_요약>\n${previous}\n</기존_요약>` : '',
      `<새_대화>\n${toTranscript(messages)}\n</새_대화>`,
      previous
        ? '기존 요약에 새 대화 내용을 합쳐 갱신된 요약을 작성해줘.'
        : '위 대화의 요약을 작성해줘.',
    ]
      .filter(Boolean)
      .join('\n\n');

    const text = await streamText({
      system: SUMMARY_SYSTEM,
      messages: [{ role: 'user', content: prompt }],
      maxTokens: SUMMARY_MAX_TOKENS,
    });

    const summary = text.trim().slice(0, MAX_SUMMARY_CHARS);
    // 빈 응답이면 기억을 잃지 않도록 기존 요약을 유지한다.
    return jsonResponse({ summary: summary || previous });
  } catch (error) {
    console.error('[api/summarize] 요약 실패:', error);
    return jsonResponse({ summary: previous });
  }
}
