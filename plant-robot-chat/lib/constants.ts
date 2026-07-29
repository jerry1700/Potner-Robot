/**
 * 클라이언트에서도 안전하게 import할 수 있는 상수만 모아둔다.
 * (서버 전용 로직 — 환경변수, API 키 — 은 lib/config.ts 에 있다)
 */

/** 사용자 입력 최대 길이 */
export const MAX_INPUT_CHARS = 4000;

/** 모델 응답 최대 길이 (초과 시 자연스럽게 끊는다) */
export const MAX_OUTPUT_CHARS = 2000;

/** 프롬프트에 그대로 넣는 최근 메시지 수. 그 이전은 요약으로 대체한다. */
export const RECENT_MESSAGE_WINDOW = 20;

/** 요약을 트리거하는 대략적인 누적 토큰 추정치 */
export const SUMMARIZE_TOKEN_THRESHOLD = 80_000;
