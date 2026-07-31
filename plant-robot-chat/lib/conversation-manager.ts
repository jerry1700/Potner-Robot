/**
 * 대화 저장소 + 히스토리 가공 (순수 함수).
 *
 * 이 모듈은 클라이언트에서만 의미가 있지만, 클라이언트 컴포넌트도 서버에서 한 번
 * 렌더되므로 localStorage 접근 지점마다 window 가드를 둔다. 가드 없이 접근하면
 * 빌드/SSR 단계에서 ReferenceError로 페이지 전체가 죽는다.
 *
 * config.ts에서 상수만 가져온다 (getServerConfig 같은 서버 전용 함수는 참조하지 않으므로
 * 클라이언트 번들에 비밀이 섞이지 않는다).
 */

import { RECENT_MESSAGE_WINDOW } from '@/lib/constants';
import type { ChatHistoryItem, Conversation, Message } from '@/types/chat';

const STORAGE_KEY = 'potner-conversations';

/** 제목 자동 생성 대상을 판별하는 기준값. use-chat이 이 값과 비교해 /api/title 호출 여부를 정한다. */
export const DEFAULT_CONVERSATION_TITLE = '새 대화';

// ------------------------------------------------------------------- id 생성

export function newId(): string {
  // crypto.randomUUID는 보안 컨텍스트(https/localhost)에서만 존재 → 없으면 충돌 확률이
  // 무시할 만한 대체값을 쓴다 (localStorage 안에서만 유일하면 충분).
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID();
  }
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

// ------------------------------------------------------------- 토큰 추정

/**
 * 한글은 영어보다 문자당 토큰 비용이 훨씬 크다. Claude의 BPE는 한글 음절 하나를
 * 보통 1~2 토큰으로 쪼개는 반면 영문은 평균 4자당 1토큰이다.
 * 그래서 `length / 4` 같은 계산은 한국어 대화에서 실제 사용량을 3~5배 과소평가한다.
 * → CJK/한글은 문자당 1.3토큰, 나머지는 4자당 1토큰으로 가중해 보수적으로 잡는다.
 *
 * 범위: 한글 자모(1100-11FF, 3130-318F, A960-A97F, D7B0-D7FF)·음절(AC00-D7A3),
 * 가나(3040-30FF, 31F0-31FF), 한자(3400-4DBF, 4E00-9FFF, F900-FAFF).
 * 문자 리터럴 대신 \u 이스케이프로 적는다 — 파일 인코딩이 한 번 깨지면 판정 자체가
 * 조용히 망가져서 원인을 찾기 어렵기 때문.
 *
 * 요약 트리거는 더 이상 이 값을 쓰지 않지만(메시지 수 창 기준으로 통일), 모듈 공개
 * 계약에 포함된 함수라 호출부용으로 남겨 둔다.
 */
const CJK_PATTERN =
  /[\u1100-\u11FF\u3040-\u30FF\u3130-\u318F\u31F0-\u31FF\u3400-\u4DBF\u4E00-\u9FFF\uA960-\uA97F\uAC00-\uD7A3\uD7B0-\uD7FF\uF900-\uFAFF]/g;

export function estimateTokens(text: string): number {
  if (!text) return 0;
  const cjkCount = text.match(CJK_PATTERN)?.length ?? 0;
  const rest = Math.max(0, text.length - cjkCount);
  return Math.ceil(cjkCount * 1.3 + rest / 4);
}

// ------------------------------------------------------------- 저장소 I/O

function isMessage(value: unknown): value is Message {
  if (typeof value !== 'object' || value === null) return false;
  const m = value as Record<string, unknown>;
  return (
    typeof m.id === 'string' &&
    (m.role === 'user' || m.role === 'assistant') &&
    typeof m.content === 'string' &&
    typeof m.timestamp === 'string'
  );
}

function isConversationShape(value: unknown): boolean {
  if (typeof value !== 'object' || value === null) return false;
  const c = value as Record<string, unknown>;
  return typeof c.id === 'string' && typeof c.title === 'string' && Array.isArray(c.messages);
}

/** 저장된 레코드를 현재 타입으로 정규화한다. */
function normalize(value: unknown): Conversation {
  const c = value as Record<string, unknown>;
  const now = new Date().toISOString();
  const messages = (c.messages as unknown[])
    .filter(isMessage)
    // 스트리밍 중 탭이 닫히면 isStreaming=true가 그대로 남아 UI가 영원히 타이핑 상태가 된다.
    .map((m) => ({ ...m, isStreaming: false }));

  return {
    id: c.id as string,
    title: (c.title as string) || DEFAULT_CONVERSATION_TITLE,
    messages,
    createdAt: typeof c.createdAt === 'string' ? c.createdAt : now,
    updatedAt: typeof c.updatedAt === 'string' ? c.updatedAt : now,
    summary: typeof c.summary === 'string' && c.summary ? c.summary : undefined,
  };
}

export function loadConversations(): Conversation[] {
  if (typeof window === 'undefined') return [];

  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];

    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];

    // 레코드 하나가 깨져도 사이드바 전체가 죽지 않도록 개별 검사 후 걸러낸다.
    return parsed.filter(isConversationShape).map(normalize);
  } catch {
    return [];
  }
}

export function saveConversations(list: Conversation[]): void {
  if (typeof window === 'undefined') return;

  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(list));
  } catch {
    // 용량 초과(QuotaExceeded)나 사생활 보호 모드에서 실패할 수 있다.
    // 대화 자체는 메모리에 남아 있으므로 저장 실패를 사용자에게 알리지 않는다.
  }
}

export function createConversation(): Conversation {
  const now = new Date().toISOString();
  return {
    id: newId(),
    title: DEFAULT_CONVERSATION_TITLE,
    messages: [],
    createdAt: now,
    updatedAt: now,
  };
}

// ------------------------------------------------------- 히스토리 / 요약

export function toHistory(messages: Message[]): ChatHistoryItem[] {
  // 빈 텍스트 블록은 Anthropic API가 400으로 거부한다 → 스트리밍 placeholder 등은 제외.
  return messages
    .filter((m) => !m.isStreaming && m.content.trim().length > 0)
    .map((m) => ({ role: m.role, content: m.content }));
}

export function needsSummarization(c: Conversation): boolean {
  // 최근 창 안에 다 들어가면 잘라낼 게 없으므로 요약이 무의미하다.
  if (c.messages.length <= RECENT_MESSAGE_WINDOW) return false;

  // 창을 넘긴 순간 바로 요약한다. 서버(/api/chat)는 받은 히스토리를 무조건 최근 창으로
  // 자르므로, 여기서 토큰 추정치 같은 다른 조건을 더 걸면 그 사이 구간(21~40개)의
  // 오래된 턴이 요약도 없이 프롬프트에서 그냥 사라진다.
  // splitForSummarization이 창 밖으로 밀려난 턴만 돌려주고 /api/summarize가 previousSummary를
  // 접어 넣으므로, 매 턴 새로 밀려난 몇 턴만 요약하면 된다.
  return true;
}

export function splitForSummarization(c: Conversation): {
  toSummarize: ChatHistoryItem[];
  keep: Message[];
} {
  const messages = c.messages;
  if (messages.length <= RECENT_MESSAGE_WINDOW) {
    return { toSummarize: [], keep: [...messages] };
  }

  let cut = messages.length - RECENT_MESSAGE_WINDOW;
  // 히스토리의 첫 턴은 user여야 한다 (assistant로 시작하면 API가 거부).
  while (cut < messages.length && messages[cut].role !== 'user') cut += 1;

  return {
    toSummarize: toHistory(messages.slice(0, cut)),
    keep: messages.slice(cut),
  };
}

// --------------------------------------------------------------- 내보내기

function formatDateTime(iso: string): string {
  const d = new Date(iso);
  // 손상된 레코드 때문에 내보내기 전체가 실패하지 않게 문구로 대체한다.
  if (Number.isNaN(d.getTime())) return '알 수 없음';
  const p = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

export function exportConversationText(c: Conversation): string {
  const divider = '─'.repeat(40);
  const lines: string[] = [
    '초록이와의 대화 기록',
    divider,
    `제목: ${c.title}`,
    `시작: ${formatDateTime(c.createdAt)}`,
    `마지막 대화: ${formatDateTime(c.updatedAt)}`,
    `메시지 수: ${c.messages.length}개`,
    divider,
    '',
  ];

  if (c.summary) {
    lines.push('[이전 대화 요약]', c.summary, '', divider, '');
  }

  for (const m of c.messages) {
    const who = m.role === 'user' ? '나' : '초록이';
    lines.push(`[${formatDateTime(m.timestamp)}] ${who}`);
    lines.push(m.content.trim() || '(내용 없음)');

    if (m.toolUse?.length) {
      lines.push(`(사용한 도구: ${m.toolUse.map((t) => t.name).join(', ')})`);
    }
    lines.push('');
  }

  lines.push(divider, '초록이 — 반려식물로봇');
  return lines.join('\n');
}
