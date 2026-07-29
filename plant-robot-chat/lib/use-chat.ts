'use client';

/**
 * 채팅 상태 훅. 대화 목록(localStorage) + /api/chat SSE 스트리밍을 한곳에서 관리한다.
 *
 * 설계상 주의점 두 가지:
 *  1) localStorage는 렌더 중에 읽지 않는다. 서버 HTML과 첫 클라이언트 렌더가 달라지면
 *     Next가 hydration mismatch를 던진다 → useEffect에서만 읽는다.
 *  2) 스트리밍은 한 턴에 수백 번 상태를 갱신한다. setState 큐를 기다리면 델타가 유실되므로
 *     최신 대화 목록을 ref(listRef)로 들고 다니며 그 값을 기준으로 갱신한다.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import {
  DEFAULT_CONVERSATION_TITLE,
  createConversation,
  loadConversations,
  needsSummarization,
  newId,
  saveConversations,
  splitForSummarization,
  toHistory,
} from '@/lib/conversation-manager';
import type {
  ChatHistoryItem,
  ChatRequestBody,
  ChatStreamEvent,
  ClientLocation,
  Conversation,
  Message,
  SummarizeRequestBody,
  SummarizeResponseBody,
  TitleRequestBody,
  TitleResponseBody,
} from '@/types/chat';

export interface UseChatResult {
  conversations: Conversation[];
  activeId: string | null;
  active: Conversation | null;
  isStreaming: boolean;
  toolStatus: string | null;
  error: string | null;
  sendMessage: (text: string) => Promise<void>;
  stop: () => void;
  newConversation: () => void;
  selectConversation: (id: string) => void;
  deleteConversation: (id: string) => void;
  clearError: () => void;
}

const GENERIC_ERROR = '초록이와 연결이 잠깐 끊겼어요. 잠시 후 다시 말 걸어주세요 🌱';

const JSON_HEADERS = { 'Content-Type': 'application/json' } as const;

/** abort는 사용자가 의도한 중단이므로 에러로 취급하지 않는다. */
function isAbortError(e: unknown): boolean {
  return typeof e === 'object' && e !== null && (e as { name?: unknown }).name === 'AbortError';
}

/** 한 SSE 프레임에서 data 줄만 이어붙인다 (event: 줄은 JSON의 type과 중복이라 무시). */
function dataOf(frame: string): string {
  const parts: string[] = [];
  for (const line of frame.split('\n')) {
    if (line.startsWith('data:')) parts.push(line.slice(5).trimStart());
  }
  return parts.join('\n');
}

/** 서버 JSON을 신뢰하지 않고 type 유무만 확인해 좁힌다. */
function parseEvent(data: string): ChatStreamEvent | null {
  try {
    const parsed: unknown = JSON.parse(data);
    if (typeof parsed !== 'object' || parsed === null) return null;
    if (typeof (parsed as { type?: unknown }).type !== 'string') return null;
    return parsed as ChatStreamEvent;
  } catch {
    return null;
  }
}

/** 비-200 응답은 SSE가 아니라 JSON 에러 바디로 온다 (rate limit / 400 / 403 / 500). */
async function readErrorBody(res: Response): Promise<string> {
  try {
    const data: unknown = await res.json();
    if (typeof data === 'object' && data !== null) {
      const d = data as { error?: unknown; message?: unknown };
      if (typeof d.error === 'string' && d.error) return d.error;
      if (typeof d.message === 'string' && d.message) return d.message;
    }
  } catch {
    // JSON이 아니면 상태 코드로 안내한다.
  }
  if (res.status === 429) return '잠깐 쉬고 올게요. 조금 후에 다시 말해줘요 🌙';
  if (res.status === 403) return '허용되지 않은 요청이에요. 페이지를 새로고침해 주세요.';
  return GENERIC_ERROR;
}

export function useChat(): UseChatResult {
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [activeId, setActiveIdState] = useState<string | null>(null);
  const [isStreaming, setIsStreaming] = useState(false);
  const [toolStatus, setToolStatus] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [hydrated, setHydrated] = useState(false);

  const listRef = useRef<Conversation[]>([]);
  const activeIdRef = useRef<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const streamingRef = useRef(false);
  /** 날씨 질문에 쓸 위치. 권한이 없거나 지원 안 되면 undefined로 남고, 그때는 도시를 물어보는 기존 흐름대로 동작한다. */
  const locationRef = useRef<ClientLocation | undefined>(undefined);

  // 대화 시작 시 한 번만 요청한다 — 메시지마다 다시 물으면 사용자가 매번 권한 팝업을 본다.
  useEffect(() => {
    if (typeof navigator === 'undefined' || !navigator.geolocation) return;
    navigator.geolocation.getCurrentPosition(
      (position) => {
        locationRef.current = {
          latitude: position.coords.latitude,
          longitude: position.coords.longitude,
        };
      },
      () => {
        // 거부·타임아웃 등은 조용히 무시한다 — city를 되물어보는 기존 대화 흐름으로 대체된다.
      },
      { enableHighAccuracy: false, timeout: 10_000, maximumAge: 30 * 60 * 1000 },
    );
  }, []);

  const commit = useCallback((updater: (prev: Conversation[]) => Conversation[]) => {
    const next = updater(listRef.current);
    listRef.current = next;
    setConversations(next);
  }, []);

  const setActive = useCallback((id: string | null) => {
    activeIdRef.current = id;
    setActiveIdState(id);
  }, []);

  const cancelStream = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    streamingRef.current = false;
    setIsStreaming(false);
    setToolStatus(null);
  }, []);

  // 저장소 로드는 마운트 후에만 (hydration mismatch 방지)
  useEffect(() => {
    const stored = loadConversations();
    const list = stored.length > 0 ? stored : [createConversation()];
    listRef.current = list;
    setConversations(list);
    setActive(list[0].id);
    setHydrated(true);
  }, [setActive]);

  // hydrated 이전에는 저장하지 않는다 — 빈 초기 상태로 기존 기록을 덮어쓰게 된다.
  // 스트리밍 중에는 델타마다 직렬화하면 렉이 생기므로, 끝난 뒤 한 번에 저장한다.
  useEffect(() => {
    if (!hydrated || isStreaming) return;
    saveConversations(conversations);
  }, [conversations, hydrated, isStreaming]);

  // 페이지를 떠날 때 진행 중인 요청을 정리한다.
  useEffect(() => () => abortRef.current?.abort(), []);

  const active = useMemo(
    () => conversations.find((c) => c.id === activeId) ?? null,
    [conversations, activeId],
  );

  /** 첫 응답이 끝난 뒤 기본 제목이면 갱신. 실패는 조용히 무시(대화 흐름과 무관). */
  const maybeGenerateTitle = useCallback(
    async (convId: string, firstUserMessage: string) => {
      const conv = listRef.current.find((c) => c.id === convId);
      if (!conv || conv.title !== DEFAULT_CONVERSATION_TITLE) return;

      try {
        const body: TitleRequestBody = { firstUserMessage };
        const res = await fetch('/api/title', {
          method: 'POST',
          headers: JSON_HEADERS,
          body: JSON.stringify(body),
        });
        if (!res.ok) return;

        const data = (await res.json()) as TitleResponseBody;
        const title = typeof data.title === 'string' ? data.title.trim() : '';
        if (!title) return;

        commit((prev) => prev.map((c) => (c.id === convId ? { ...c, title } : c)));
      } catch {
        // 제목이 없어도 대화는 정상 동작한다.
      }
    },
    [commit],
  );

  const sendMessage = useCallback(
    async (text: string) => {
      const content = text.trim();
      if (!content || streamingRef.current) return;

      setError(null);
      setToolStatus(null);

      // 활성 대화가 없거나(초기 진입) 방금 삭제됐으면 새로 만든다.
      let convId = activeIdRef.current;
      if (!convId || !listRef.current.some((c) => c.id === convId)) {
        const created = createConversation();
        commit((prev) => [created, ...prev]);
        setActive(created.id);
        convId = created.id;
      }
      const targetId = convId;

      const target = listRef.current.find((c) => c.id === targetId);
      if (!target) return;

      const now = new Date().toISOString();
      const userMessage: Message = { id: newId(), role: 'user', content, timestamp: now };
      const assistantId = newId();
      const assistantMessage: Message = {
        id: assistantId,
        role: 'assistant',
        content: '',
        timestamp: now,
        isStreaming: true,
      };

      // 히스토리는 이번 사용자 메시지를 붙이기 전 상태로 뽑는다 (서버가 message로 따로 받음).
      // 요약이 필요할 때만 최근 창으로 자른다 — 항상 자르면 요약도 없이 앞부분이 그냥
      // 사라져서, 초록이가 조금 전에 들은 식물 이름을 잊어버린다.
      const shouldTrim = needsSummarization(target);
      const { toSummarize, keep } = shouldTrim
        ? splitForSummarization(target)
        : { toSummarize: [] as ChatHistoryItem[], keep: target.messages };
      const history: ChatHistoryItem[] = toHistory(keep);
      const shouldSummarize = shouldTrim && toSummarize.length > 0;
      let summary = target.summary;

      // 낙관적 렌더: 사용자 메시지 + 빈 assistant 말풍선을 먼저 붙인다.
      commit((prev) =>
        prev.map((c) =>
          c.id === targetId
            ? { ...c, messages: [...c.messages, userMessage, assistantMessage], updatedAt: now }
            : c,
        ),
      );

      const controller = new AbortController();
      abortRef.current = controller;
      streamingRef.current = true;
      setIsStreaming(true);

      // 대화 목록에서 해당 대화가 사라졌으면(삭제됨) map이 아무것도 못 맞춰 자동으로 no-op이 된다.
      const patchAssistant = (patch: (m: Message) => Message) => {
        commit((prev) =>
          prev.map((c) =>
            c.id === targetId
              ? { ...c, messages: c.messages.map((m) => (m.id === assistantId ? patch(m) : m)) }
              : c,
          ),
        );
      };

      if (shouldSummarize) {
        // 요약 실패는 치명적이지 않다 — 요약 없이 최근 창만 보낸다.
        try {
          const body: SummarizeRequestBody = {
            messages: toSummarize,
            previousSummary: target.summary,
          };
          const res = await fetch('/api/summarize', {
            method: 'POST',
            headers: JSON_HEADERS,
            body: JSON.stringify(body),
            signal: controller.signal,
          });
          if (res.ok) {
            const data = (await res.json()) as SummarizeResponseBody;
            if (typeof data.summary === 'string' && data.summary.trim()) {
              summary = data.summary.trim();
              commit((prev) => prev.map((c) => (c.id === targetId ? { ...c, summary } : c)));
            }
          }
        } catch {
          // 무시
        }
      }

      let streamed = '';
      let failed = false;

      /** 프레임 1개 처리. true면 더 읽지 않고 끊는다. */
      const handleEvent = (event: ChatStreamEvent): boolean => {
        switch (event.type) {
          case 'text_delta':
            streamed += event.text;
            patchAssistant((m) => ({ ...m, content: streamed }));
            setToolStatus(null);
            return false;

          case 'tool_use_start':
            setToolStatus(event.status);
            return false;

          case 'tool_use_end':
            setToolStatus(null);
            return false;

          case 'message_complete':
            patchAssistant((m) => ({
              ...m,
              content: streamed,
              isStreaming: false,
              // 서버 JSON을 검증하지 않으므로 비어 있으면 기존 값을 유지한다.
              toolUse: event.toolUse?.length ? event.toolUse : m.toolUse,
            }));
            setToolStatus(null);
            return false;

          case 'error':
            setError(event.message || GENERIC_ERROR);
            failed = true;
            return true;

          default:
            // 서버가 나중에 이벤트를 추가해도 클라이언트가 죽지 않게 무시한다.
            return false;
        }
      };

      try {
        const requestBody: ChatRequestBody = {
          message: content,
          history,
          summary,
          conversationId: targetId,
          location: locationRef.current,
        };

        const res = await fetch('/api/chat', {
          method: 'POST',
          headers: JSON_HEADERS,
          body: JSON.stringify(requestBody),
          signal: controller.signal,
        });

        if (!res.ok || !res.body) {
          setError(await readErrorBody(res));
          failed = true;
        } else {
          const reader = res.body.getReader();
          const decoder = new TextDecoder();
          let buffer = '';
          let stopReading = false;

          while (!stopReading) {
            const { value, done } = await reader.read();

            if (done) {
              // 서버가 마지막 프레임을 빈 줄로 닫지 않고 끝낼 수 있다 →
              // 남은 버퍼를 마지막 프레임으로 한 번 더 처리해야 message_complete를 놓치지 않는다.
              const rest = dataOf(buffer + decoder.decode());
              const event = rest ? parseEvent(rest) : null;
              if (event) handleEvent(event);
              break;
            }
            if (!value) continue;

            // 한글은 멀티바이트라 청크 경계에서 쪼개진다 → decode에 stream:true 필수.
            buffer = (buffer + decoder.decode(value, { stream: true })).replace(/\r\n/g, '\n');

            // 프레임 경계는 빈 줄. 청크가 프레임 중간에서 끊길 수 있으므로
            // 완성된 프레임만 소비하고 나머지는 버퍼에 남긴다.
            let boundary = buffer.indexOf('\n\n');
            while (boundary !== -1) {
              const raw = dataOf(buffer.slice(0, boundary));
              buffer = buffer.slice(boundary + 2);

              const event = raw ? parseEvent(raw) : null;
              if (event && handleEvent(event)) {
                stopReading = true;
                break;
              }

              boundary = buffer.indexOf('\n\n');
            }
          }

          if (stopReading) {
            // 에러로 중단했으면 커넥션을 붙잡고 있지 않게 정리한다.
            try {
              await reader.cancel();
            } catch {
              // 이미 닫혔으면 무시
            }
          }
        }
      } catch (e) {
        if (!isAbortError(e)) {
          setError(GENERIC_ERROR);
          failed = true;
        }
      } finally {
        streamingRef.current = false;
        setIsStreaming(false);
        setToolStatus(null);
        if (abortRef.current === controller) abortRef.current = null;

        if (streamed.trim()) {
          // 어떤 경로로 끝나도 타이핑 인디케이터가 남지 않게 한다.
          patchAssistant((m) =>
            m.isStreaming ? { ...m, content: streamed, isStreaming: false } : m,
          );
        } else {
          // 한 글자도 못 받았으면 빈 말풍선을 남기지 않는다.
          commit((prev) =>
            prev.map((c) =>
              c.id === targetId
                ? { ...c, messages: c.messages.filter((m) => m.id !== assistantId) }
                : c,
            ),
          );
        }

        commit((prev) =>
          prev.map((c) =>
            c.id === targetId ? { ...c, updatedAt: new Date().toISOString() } : c,
          ),
        );
      }

      if (!failed && streamed.trim()) {
        await maybeGenerateTitle(targetId, content);
      }
    },
    [commit, maybeGenerateTitle, setActive],
  );

  const stop = useCallback(() => {
    cancelStream();
    // 이미 흘러온 텍스트는 그대로 두고 타이핑 상태만 끈다.
    commit((prev) =>
      prev.map((c) => ({
        ...c,
        messages: c.messages.map((m) => (m.isStreaming ? { ...m, isStreaming: false } : m)),
      })),
    );
  }, [cancelStream, commit]);

  const newConversation = useCallback(() => {
    cancelStream();
    setError(null);

    // 이미 비어 있는 대화를 보고 있으면 빈 대화를 또 만들지 않는다.
    const current = listRef.current.find((c) => c.id === activeIdRef.current);
    if (current && current.messages.length === 0) return;

    const created = createConversation();
    commit((prev) => [created, ...prev]);
    setActive(created.id);
  }, [cancelStream, commit, setActive]);

  const selectConversation = useCallback(
    (id: string) => {
      if (!listRef.current.some((c) => c.id === id)) return;
      setError(null);
      setActive(id);
    },
    [setActive],
  );

  const deleteConversation = useCallback(
    (id: string) => {
      const wasActive = activeIdRef.current === id;
      if (wasActive) cancelStream();

      const remaining = listRef.current.filter((c) => c.id !== id);
      // 목록이 비면 곧바로 새 대화를 만들어 activeId가 항상 실재하는 대화를 가리키게 한다.
      const next = remaining.length > 0 ? remaining : [createConversation()];
      commit(() => next);

      if (wasActive || !next.some((c) => c.id === activeIdRef.current)) {
        setActive(next[0]?.id ?? null);
      }
    },
    [cancelStream, commit, setActive],
  );

  const clearError = useCallback(() => setError(null), []);

  return {
    conversations,
    activeId,
    active,
    isStreaming,
    toolStatus,
    error,
    sendMessage,
    stop,
    newConversation,
    selectConversation,
    deleteConversation,
    clearError,
  };
}
