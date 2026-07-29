'use client';

import { useCallback, useEffect, useRef, useState } from 'react';

import { InputBar } from '@/components/InputBar';
import { MessageBubble } from '@/components/MessageBubble';
import { PlantAvatar } from '@/components/PlantAvatar';
import { Sidebar } from '@/components/Sidebar';
import { Toast } from '@/components/Toast';
import { TypingIndicator } from '@/components/TypingIndicator';
import { exportConversationText } from '@/lib/conversation-manager';
import { SUGGESTED_QUESTIONS, WELCOME_MESSAGE } from '@/lib/system-prompt';
import { useChat } from '@/lib/use-chat';

/** 이 거리(px) 안쪽이면 "사용자가 맨 아래를 보고 있다"고 본다. */
const STICK_THRESHOLD_PX = 96;

/** 파일명에 못 쓰는 문자 (윈도우 기준이 가장 좁으므로 그쪽에 맞춘다) */
const UNSAFE_FILENAME_CHARS = /[\\/:*?"<>|]/g;

const FALLBACK_EXPORT_NAME = '초록이-대화';

export function ChatWindow() {
  const {
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
  } = useChat();

  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [showJumpButton, setShowJumpButton] = useState(false);

  const listRef = useRef<HTMLDivElement | null>(null);
  /**
   * 자동 스크롤 여부는 state가 아니라 ref로 든다.
   * 스트리밍은 한 턴에 수백 번 리렌더되는데, state면 이펙트가 한 틱 전 값을 보고 판단해
   * 사용자가 위로 올린 직후에도 한 번 더 끌어내린다.
   */
  const stickToBottom = useRef(true);

  const messages = active?.messages ?? [];
  const lastMessage = messages.length > 0 ? messages[messages.length - 1] : undefined;
  const hasStreamingText =
    lastMessage !== undefined && lastMessage.role === 'assistant' && lastMessage.content.length > 0;

  /**
   * 인디케이터는 (1) 첫 토큰을 기다릴 때, (2) 도구가 도는 중일 때만 띄운다.
   * 글자가 이미 흘러나오는 동안에도 띄우면 말풍선과 점 세 개가 겹쳐 산만하다.
   */
  const showTyping = isStreaming && (toolStatus !== null || !hasStreamingText);

  /** 메시지 수만 보면 델타 단위 변화를 놓치므로 마지막 글자 수까지 서명에 넣는다. */
  const contentSignature = `${messages.length}:${lastMessage?.content.length ?? 0}`;

  const scrollToBottom = useCallback((smooth = false) => {
    const el = listRef.current;
    if (!el) return;
    el.scrollTo({ top: el.scrollHeight, behavior: smooth ? 'smooth' : 'auto' });
  }, []);

  const handleScroll = useCallback(() => {
    const el = listRef.current;
    if (!el) return;
    const distanceFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
    const stick = distanceFromBottom < STICK_THRESHOLD_PX;
    stickToBottom.current = stick;
    setShowJumpButton((prev) => (prev === !stick ? prev : !stick));
  }, []);

  // 위로 올려 읽는 중이면 끌어내리지 않는다 (사용자와 스크롤 다툼 금지)
  useEffect(() => {
    if (!stickToBottom.current) return;
    scrollToBottom();
  }, [contentSignature, showTyping, scrollToBottom]);

  // 대화를 바꾸면 항상 가장 최근 메시지에서 시작한다
  useEffect(() => {
    stickToBottom.current = true;
    setShowJumpButton(false);
    scrollToBottom();
  }, [activeId, scrollToBottom]);

  // 모바일 드로어는 Esc로도 닫혀야 한다 (오버레이 탭이 유일한 탈출구면 갇힌 느낌이 난다)
  useEffect(() => {
    if (!sidebarOpen) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setSidebarOpen(false);
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [sidebarOpen]);

  const handleSend = useCallback(
    (text: string) => {
      // 직접 보낸 순간에는 다시 맨 아래에 붙여준다
      stickToBottom.current = true;
      setShowJumpButton(false);
      void sendMessage(text);
    },
    [sendMessage],
  );

  const handleSelect = useCallback(
    (id: string) => {
      selectConversation(id);
      setSidebarOpen(false);
    },
    [selectConversation],
  );

  const handleNew = useCallback(() => {
    newConversation();
    setSidebarOpen(false);
  }, [newConversation]);

  const handleExport = useCallback(
    (id: string) => {
      const target = conversations.find((c) => c.id === id);
      if (!target) return;

      const blob = new Blob([exportConversationText(target)], {
        type: 'text/plain;charset=utf-8',
      });
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement('a');
      anchor.href = url;
      const safeTitle = target.title.replace(UNSAFE_FILENAME_CHARS, '_').trim();
      anchor.download = `${safeTitle || FALLBACK_EXPORT_NAME}.txt`;
      anchor.click();
      URL.revokeObjectURL(url);
    },
    [conversations],
  );

  const isEmpty = messages.length === 0;

  return (
    <div className="flex h-full overflow-hidden bg-canvas">
      <Sidebar
        conversations={conversations}
        activeId={activeId}
        onSelect={handleSelect}
        onNew={handleNew}
        onDelete={deleteConversation}
        onExport={handleExport}
        open={sidebarOpen}
        onClose={() => setSidebarOpen(false)}
      />

      <div className="flex min-w-0 flex-1 flex-col">
        {/* 색상 토큰이 CSS 변수라 tailwind 투명도 수정자(/90)가 안 먹는다 → 불투명 배경으로 둔다 */}
        <header className="flex shrink-0 items-center gap-3 border-b border-edge bg-surface px-4 py-3">
          <button
            type="button"
            onClick={() => setSidebarOpen(true)}
            aria-label="대화 목록 열기"
            aria-expanded={sidebarOpen}
            className="-ml-1 rounded-lg p-2 text-ink-muted transition-colors hover:bg-surface-alt hover:text-ink md:hidden"
          >
            <svg
              viewBox="0 0 24 24"
              width="20"
              height="20"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.8"
              strokeLinecap="round"
              aria-hidden="true"
            >
              <path d="M4 7h16M4 12h16M4 17h16" />
            </svg>
          </button>

          <PlantAvatar size={36} />

          <div className="min-w-0">
            <h1 className="truncate text-base font-semibold leading-tight text-ink">초록이</h1>
            <p className="truncate text-xs text-ink-muted">
              {isStreaming
                ? (toolStatus ?? '생각하고 있어요...')
                : '반려식물로봇 · 언제든 물어보세요'}
            </p>
          </div>
        </header>

        <div className="relative min-h-0 flex-1">
          <div
            ref={listRef}
            onScroll={handleScroll}
            className="h-full overflow-y-auto overscroll-contain px-4 py-5"
            role="log"
            aria-live="polite"
            aria-label="초록이와의 대화"
          >
            <div className="mx-auto flex w-full max-w-3xl flex-col gap-4">
              {isEmpty ? (
                <div className="flex animate-fade-up flex-col gap-5">
                  <div className="flex items-start gap-3">
                    <div className="shrink-0 pt-1">
                      <PlantAvatar size={34} />
                    </div>
                    <div className="whitespace-pre-line rounded-2xl rounded-tl-md border border-edge bg-surface px-4 py-3 text-[0.95rem] leading-relaxed text-ink shadow-[var(--shadow-soft)]">
                      {WELCOME_MESSAGE}
                    </div>
                  </div>

                  <div className="sm:pl-12">
                    <p className="mb-2 text-xs font-medium text-ink-muted">
                      이런 걸 물어볼 수 있어요
                    </p>
                    <div className="grid gap-2 sm:grid-cols-2">
                      {SUGGESTED_QUESTIONS.map((question) => (
                        <button
                          key={question}
                          type="button"
                          onClick={() => handleSend(question)}
                          disabled={isStreaming}
                          className="rounded-xl border border-edge bg-surface px-3.5 py-3 text-left text-sm leading-snug text-ink transition-colors hover:border-leaf hover:bg-surface-alt disabled:cursor-not-allowed disabled:opacity-60"
                        >
                          {question}
                        </button>
                      ))}
                    </div>
                  </div>
                </div>
              ) : (
                messages.map((message) => <MessageBubble key={message.id} message={message} />)
              )}

              {showTyping ? (
                <div className="flex items-center gap-2.5">
                  <PlantAvatar size={30} />
                  {/* 도구가 도는 중이면 어떤 도구인지 라벨로 보여준다 */}
                  <TypingIndicator label={toolStatus ?? undefined} />
                </div>
              ) : null}
            </div>
          </div>

          {showJumpButton ? (
            <button
              type="button"
              onClick={() => {
                stickToBottom.current = true;
                setShowJumpButton(false);
                scrollToBottom(true);
              }}
              className="absolute bottom-4 left-1/2 -translate-x-1/2 rounded-full border border-edge bg-surface px-3.5 py-2 text-xs font-medium text-ink shadow-[var(--shadow-soft)] transition-colors hover:bg-surface-alt"
            >
              ↓ 최근 대화 보기
            </button>
          ) : null}
        </div>

        {/* InputBar가 자체적으로 상단 테두리와 max-width를 갖고 있어 여기서 또 감싸지 않는다 */}
        <div className="shrink-0">
          <InputBar
            onSend={handleSend}
            onStop={stop}
            isStreaming={isStreaming}
            disabled={isStreaming}
          />
        </div>
      </div>

      {error !== null ? <Toast message={error} onDismiss={clearError} /> : null}
    </div>
  );
}
