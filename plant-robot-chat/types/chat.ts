/**
 * 앱 전역 타입 계약. 모든 모듈이 이 파일을 기준으로 작성된다.
 *
 * 원칙: Anthropic API에 그대로 보내는 구조는 SDK 타입(Anthropic.MessageParam 등)을
 * 재사용하고, 여기서는 UI/저장소 계층의 타입만 정의한다.
 */

import type Anthropic from '@anthropic-ai/sdk';

// ---------------------------------------------------------------- 대화 데이터

export type Role = 'user' | 'assistant';

/** 도구 호출 1건 (UI 표시 + 히스토리 재구성용) */
export interface ToolInvocation {
  id: string;
  name: string;
  input: unknown;
  /** 핸들러 실행 결과 (JSON 직렬화된 문자열). 실패 시 isError=true */
  result?: string;
  isError?: boolean;
}

export interface Message {
  id: string;
  role: Role;
  content: string;
  /** ISO 8601 문자열로 저장한다 (localStorage 직렬화 안전) */
  timestamp: string;
  toolUse?: ToolInvocation[];
  isStreaming?: boolean;
  /** 안전 필터가 개입해 생성된 메시지인지 */
  safetyBlocked?: boolean;
}

export interface Conversation {
  id: string;
  title: string;
  messages: Message[];
  createdAt: string;
  updatedAt: string;
  /** 오래된 메시지 요약 (토큰 초과 방지) */
  summary?: string;
}

// ------------------------------------------------------- API 요청/응답 계약

/** POST /api/chat 요청 바디 */
export interface ChatRequestBody {
  /** 사용자가 방금 보낸 메시지 */
  message: string;
  /** 이전 대화 (서버는 상태를 갖지 않는다) */
  history: ChatHistoryItem[];
  /** 오래된 대화 요약 (있으면 시스템 프롬프트에 삽입) */
  summary?: string;
  /** rate limit 및 로깅용 세션 구분자 */
  conversationId?: string;
  /** 브라우저 Geolocation으로 얻은 사용자 위치 (권한 거부 시 생략) */
  location?: ClientLocation;
}

/** 브라우저에서 보내는 위/경도. 도시 매칭은 서버가 한다(클라이언트는 원시 좌표만 안다). */
export interface ClientLocation {
  latitude: number;
  longitude: number;
}

/**
 * 로봇(하드웨어)이 /api/sensor 로 밀어 넣는 최신 센서 값.
 * 항목은 전부 선택값이다 — 로봇마다 장착된 센서 구성이 다를 수 있다.
 */
export interface SensorReading {
  soilMoisturePercent?: number;
  temperatureCelsius?: number;
  humidityPercent?: number;
  lightLux?: number;
  /** 서버가 값을 받은 시각 (ISO 8601). 클라이언트가 채우지 않고 서버가 채운다. */
  measuredAt: string;
}

/** POST /api/sensor 요청 바디. measuredAt은 서버가 수신 시각으로 덮어쓴다. */
export type SensorIngestBody = Omit<SensorReading, 'measuredAt'>;

/** 히스토리 1턴. 서버에서 Anthropic.MessageParam으로 변환된다. */
export interface ChatHistoryItem {
  role: Role;
  content: string;
}

/** POST /api/title 요청/응답 */
export interface TitleRequestBody {
  firstUserMessage: string;
}
export interface TitleResponseBody {
  title: string;
}

/** POST /api/summarize 요청/응답 */
export interface SummarizeRequestBody {
  messages: ChatHistoryItem[];
  /** 기존 요약이 있으면 누적 갱신 */
  previousSummary?: string;
}
export interface SummarizeResponseBody {
  summary: string;
}

// ------------------------------------------------------------- SSE 이벤트

/**
 * /api/chat 이 흘려보내는 SSE 이벤트.
 * 와이어 포맷: `event: <type>\ndata: <json>\n\n`
 */
export type ChatStreamEvent =
  | { type: 'text_delta'; text: string }
  | { type: 'tool_use_start'; tool: string; status: string }
  | { type: 'tool_use_end'; tool: string; status: string; isError?: boolean }
  | { type: 'message_complete'; usage: TokenUsage; toolUse: ToolInvocation[] }
  | { type: 'error'; message: string };

export interface TokenUsage {
  input_tokens: number;
  output_tokens: number;
}

// ------------------------------------------------------------- 안전 필터

export type SafetyCategory =
  | 'harmful'
  | 'off_topic_sensitive'
  | 'prompt_injection'
  | 'personal_info';

export interface SafetyCheckResult {
  safe: boolean;
  category?: SafetyCategory;
  /** 차단 시 초록이가 대신 할 말 */
  fallbackResponse?: string;
  /** true면 이 메시지를 대화 히스토리에 저장하지 않는다 (개인정보 등) */
  doNotPersist?: boolean;
}

// ------------------------------------------------------------- 도구 계층

/** 도구 핸들러 서명. 모든 핸들러는 JSON 직렬화 가능한 값을 반환한다. */
export type ToolHandler = (input: Record<string, unknown>) => Promise<unknown> | unknown;

/** 도구 이름 → 사용자에게 보여줄 진행 상태 문구 */
export type ToolStatusMap = Record<string, string>;

/** SDK 도구 정의 배열의 별칭 */
export type ToolDefinitions = Anthropic.Tool[];
