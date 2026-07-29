/**
 * Anthropic(SSAFY GMS 프록시) 클라이언트. 서버 전용.
 *
 * 이 파일의 모든 호출이 스트리밍인 이유: GMS 게이트웨이는 비-스트리밍 응답 바디의
 * 앞부분을 잘라서 보내기 때문에 JSON 파싱이 깨진다(실측). 그래서 "한 방 응답"이
 * 필요한 곳도 messages.stream() + finalMessage() 로 받는다.
 *
 * temperature / top_p / top_k 는 claude-opus-4-7 / 4-8 에서 400이므로 넣지 않는다.
 */

import Anthropic from '@anthropic-ai/sdk';

import { MAX_TOKENS, getServerConfig } from '@/lib/config';

/** 요청마다 새 클라이언트를 만들면 커넥션 풀이 낭비되므로 설정 단위로 재사용한다. */
let cached: { apiKey: string; baseURL: string; client: Anthropic } | null = null;

export function getClient(): Anthropic {
  const { apiKey, baseURL } = getServerConfig();

  if (cached && cached.apiKey === apiKey && cached.baseURL === baseURL) {
    return cached.client;
  }

  const client = new Anthropic({ apiKey, baseURL });
  cached = { apiKey, baseURL, client };
  return client;
}

/**
 * 제목 생성·요약처럼 최종 텍스트 한 덩어리만 필요한 호출용.
 * 스트리밍으로 받아 텍스트 블록만 이어붙인다(위 주석의 게이트웨이 버그 회피).
 */
export async function streamText(params: {
  system: string;
  messages: Anthropic.MessageParam[];
  maxTokens?: number;
}): Promise<string> {
  const { model } = getServerConfig();

  const stream = getClient().messages.stream({
    model,
    max_tokens: params.maxTokens ?? MAX_TOKENS,
    system: params.system,
    messages: params.messages,
  });

  const final = await stream.finalMessage();

  let text = '';
  for (const block of final.content) {
    if (block.type === 'text') text += block.text;
  }
  return text.trim();
}
