/**
 * POST /api/title — 대화 제목 자동 생성.
 *
 * 제목은 장식용이므로 어떤 실패에서도 500을 내지 않는다.
 * (제목 요청이 깨져서 채팅 UI가 멈추면 안 된다 → 항상 200 + 대체 제목)
 */

import { streamText } from '@/lib/anthropic';
import { isAllowedOrigin } from '@/lib/config';
import type { TitleRequestBody, TitleResponseBody } from '@/types/chat';

export const runtime = 'nodejs';

/** 모델에게 요구하는 길이는 10자, 20자는 방어용 하드 컷 */
const MAX_TITLE_CHARS = 20;

/** 제목 생성에는 첫 메시지의 앞부분만 있으면 충분하다 (토큰 절약) */
const SOURCE_CHARS = 500;

const TITLE_MAX_TOKENS = 64;

const FALLBACK_TITLE = '새 대화';

const TITLE_SYSTEM = `너는 대화에 짧은 한국어 제목을 붙이는 도구다.

규칙:
- 10자 이내의 한국어 명사구로만 답한다.
- 따옴표, 마침표, 이모지, 마크다운 기호를 쓰지 않는다.
- 설명이나 인사 없이 제목 한 줄만 출력한다.

예시 입력: 몬스테라 잎이 노랗게 변했어요
예시 출력: 몬스테라 잎 노랗게`;

function jsonResponse(body: TitleResponseBody, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json; charset=utf-8' },
  });
}

/**
 * 모델 출력은 신뢰하지 않는다. 따옴표·마크다운·개행을 걷어내고 길이를 강제한다.
 * 하드 컷 이후에도 문장부호를 한 번 더 떼는 이유: 잘린 자리에 부호가 남을 수 있다.
 */
function sanitizeTitle(raw: string): string {
  return raw
    .replace(/[*_`#>~[\]|]/g, '') // 마크다운 기호
    .replace(/["'“”‘’「」『』]/g, '') // 각종 인용부호
    .replace(/\s+/g, ' ')
    .trim()
    .replace(/[.。,、!?！？:;…]+$/u, '') // 끝 문장부호
    .trim()
    .slice(0, MAX_TITLE_CHARS)
    .replace(/[\s.。,、!?！？:;…]+$/u, '');
}

/** 모델 없이도 쓸 수 있는 제목 — 첫 메시지를 잘라서 만든다. */
function deriveFallbackTitle(firstUserMessage: string): string {
  const cleaned = sanitizeTitle(firstUserMessage);
  if (!cleaned) return FALLBACK_TITLE;
  // 잘렸으면 말줄임표로 표시해 어색한 끊김을 줄인다.
  return cleaned.length >= MAX_TITLE_CHARS ? `${cleaned.slice(0, 18)}…` : cleaned;
}

function readFirstUserMessage(raw: unknown): string {
  if (!raw || typeof raw !== 'object') return '';
  const body = raw as Partial<TitleRequestBody>;
  return typeof body.firstUserMessage === 'string' ? body.firstUserMessage : '';
}

export async function POST(req: Request): Promise<Response> {
  // 차단 응답도 TitleResponseBody 형태를 유지해 클라이언트 파싱이 깨지지 않게 한다.
  if (!isAllowedOrigin(req)) {
    return jsonResponse({ title: FALLBACK_TITLE }, 403);
  }

  const raw: unknown = await req.json().catch(() => null);
  const firstUserMessage = readFirstUserMessage(raw).trim();
  const fallback = deriveFallbackTitle(firstUserMessage);

  if (!firstUserMessage) {
    return jsonResponse({ title: fallback });
  }

  try {
    const text = await streamText({
      system: TITLE_SYSTEM,
      messages: [
        {
          role: 'user',
          content: `다음 메시지로 시작된 대화의 제목을 만들어줘.\n\n${firstUserMessage.slice(
            0,
            SOURCE_CHARS,
          )}`,
        },
      ],
      maxTokens: TITLE_MAX_TOKENS,
    });

    const title = sanitizeTitle(text);
    return jsonResponse({ title: title || fallback });
  } catch (error) {
    console.error('[api/title] 제목 생성 실패:', error);
    return jsonResponse({ title: fallback });
  }
}
