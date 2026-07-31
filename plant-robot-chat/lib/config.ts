/**
 * 환경변수 해석 한 곳. 서버 전용 (API Route에서만 import).
 *
 * SSAFY GMS 게이트웨이 경유가 기본값이다. 실측 결과:
 *   - GMS는 api.anthropic.com 을 프록시하고, GMS 키를 x-api-key 로 받는다
 *     (Anthropic SDK 기본 인증 방식과 같아서 그대로 동작)
 *   - GMS 허용 모델: claude-opus-4-8 / 4-7 / 4-6, claude-sonnet-4-6
 *     (claude-opus-5 등 5 계열은 아직 allowlist에 없음)
 *   - ★ 비-스트리밍 응답은 바디 앞부분이 잘려서 도착한다 → 항상 스트리밍으로 호출해야 한다
 *     (lib/anthropic.ts 의 streamText 참고)
 */

// 클라이언트 안전 상수는 lib/constants.ts 로 분리했다. 기존 import 호환을 위해 re-export.
export {
  MAX_INPUT_CHARS,
  MAX_OUTPUT_CHARS,
  RECENT_MESSAGE_WINDOW,
  SUMMARIZE_TOKEN_THRESHOLD,
} from './constants';

export const GMS_ANTHROPIC_BASE_URL = 'https://gms.ssafy.io/gmsapi/api.anthropic.com';

/** GMS allowlist에 있는 최상위 모델. 직접 Anthropic 키를 쓸 경우 claude-opus-5 로 올릴 수 있다. */
export const DEFAULT_MODEL = 'claude-opus-4-8';

export const MAX_TOKENS = 4096;

/** 도구 호출 루프 최대 횟수 (무한루프 방지) */
export const MAX_TOOL_ROUNDS = 5;

/** 세션당 분당 요청 상한 */
export const RATE_LIMIT_PER_MINUTE = 20;

export interface ServerConfig {
  apiKey: string;
  baseURL: string;
  model: string;
}

/**
 * 서버 설정을 읽는다. 키가 없으면 throw — API Route가 500 + 안내 문구로 변환한다.
 */
export function getServerConfig(): ServerConfig {
  const apiKey =
    process.env.ANTHROPIC_API_KEY?.trim() || process.env.GMS_API_KEY?.trim() || '';

  if (!apiKey) {
    throw new Error(
      'ANTHROPIC_API_KEY (또는 GMS_API_KEY) 환경변수가 없습니다. .env.local 을 확인하세요.',
    );
  }

  return {
    apiKey,
    baseURL: process.env.ANTHROPIC_BASE_URL?.trim() || GMS_ANTHROPIC_BASE_URL,
    model: process.env.ANTHROPIC_MODEL?.trim() || DEFAULT_MODEL,
  };
}

/**
 * 로봇(하드웨어)이 /api/sensor 를 호출할 때 쓰는 공유 비밀키.
 * isAllowedOrigin과 달리 로봇은 브라우저가 아니라 별도 프로세스라 origin/host가 없다.
 */
export function getSensorIngestKey(): string {
  return process.env.SENSOR_INGEST_KEY?.trim() || '';
}

/**
 * 요청 origin 검증 (기본적인 CSRF 방어).
 * 같은 호스트에서 온 요청만 허용한다.
 */
export function isAllowedOrigin(request: Request): boolean {
  const origin = request.headers.get('origin');
  // fetch를 same-origin으로 부르면 origin이 없을 수 있다 — 그 경우는 허용.
  if (!origin) return true;

  const host = request.headers.get('host');
  if (!host) return false;

  try {
    return new URL(origin).host === host;
  } catch {
    return false;
  }
}
