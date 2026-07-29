'use client';

import { useEffect } from 'react';

import { PlantAvatar } from '@/components/PlantAvatar';
import type { Conversation } from '@/types/chat';

export interface SidebarProps {
  conversations: Conversation[];
  activeId: string | null;
  onSelect: (id: string) => void;
  onNew: () => void;
  onDelete: (id: string) => void;
  onExport: (id: string) => void;
  /** 모바일 오버레이 열림 여부 (데스크톱에서는 무시된다) */
  open?: boolean;
  onClose?: () => void;
}

/** 목록에서는 절대 시각보다 "얼마나 전인지"가 훨씬 빨리 읽힌다. */
function formatRelative(iso: string): string {
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return '';

  const diffMinutes = Math.floor((Date.now() - then) / 60000);
  if (diffMinutes < 1) return '방금';
  if (diffMinutes < 60) return `${diffMinutes}분 전`;

  const diffHours = Math.floor(diffMinutes / 60);
  if (diffHours < 24) return `${diffHours}시간 전`;

  const diffDays = Math.floor(diffHours / 24);
  if (diffDays < 7) return `${diffDays}일 전`;

  return new Date(then).toLocaleDateString('ko-KR', { month: 'long', day: 'numeric' });
}

export function Sidebar({
  conversations,
  activeId,
  onSelect,
  onNew,
  onDelete,
  onExport,
  open = false,
  onClose,
}: SidebarProps) {
  // 모바일 드로어는 Esc로 닫히는 게 관례다. 열려 있을 때만 리스너를 붙여 낭비를 줄인다.
  useEffect(() => {
    if (!open || !onClose) return;
    function handleKeyDown(event: globalThis.KeyboardEvent) {
      if (event.key === 'Escape') onClose?.();
    }
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [open, onClose]);

  function handleDelete(conversation: Conversation) {
    // 삭제는 localStorage에서 즉시 사라져 복구가 불가능하므로 확인을 받는다.
    if (window.confirm(`'${conversation.title}' 대화를 삭제할까요? 되돌릴 수 없어요.`)) {
      onDelete(conversation.id);
    }
  }

  return (
    <>
      {open && (
        <div
          className="fixed inset-0 z-30 bg-soil/30 backdrop-blur-sm md:hidden"
          onClick={onClose}
          aria-hidden="true"
        />
      )}

      <aside
        className={`fixed inset-y-0 left-0 z-40 flex w-[280px] shrink-0 flex-col border-r border-edge bg-surface-alt transition-transform duration-200 md:static md:z-auto md:translate-x-0 ${
          open ? 'translate-x-0' : '-translate-x-full'
        }`}
        aria-label="대화 목록"
      >
        <div className="flex items-center gap-2 px-4 py-4">
          <PlantAvatar size={28} />
          <span className="flex-1 text-sm font-bold text-ink">초록이</span>
          <button
            type="button"
            onClick={onClose}
            aria-label="목록 닫기"
            className="rounded-lg p-1.5 text-ink-muted hover:bg-surface hover:text-ink md:hidden"
          >
            <svg viewBox="0 0 20 20" className="h-4 w-4 fill-current" aria-hidden="true">
              <path d="M5.5 4.4 10 8.9l4.5-4.5 1.1 1.1L11.1 10l4.5 4.5-1.1 1.1L10 11.1l-4.5 4.5-1.1-1.1L8.9 10 4.4 5.5 5.5 4.4Z" />
            </svg>
          </button>
        </div>

        <div className="px-3">
          <button
            type="button"
            onClick={onNew}
            className="flex w-full items-center justify-center gap-1.5 rounded-xl bg-leaf px-3 py-2.5 text-sm font-semibold text-white transition-colors hover:bg-leaf-deep"
          >
            <svg viewBox="0 0 20 20" className="h-4 w-4 fill-current" aria-hidden="true">
              <path d="M9.1 3.2h1.8v5.9h5.9v1.8h-5.9v5.9H9.1v-5.9H3.2V9.1h5.9V3.2Z" />
            </svg>
            새 대화
          </button>
        </div>

        <nav className="mt-3 flex-1 space-y-1 overflow-y-auto px-2 pb-4">
          {conversations.length === 0 ? (
            <p className="px-2 py-6 text-center text-xs leading-relaxed text-ink-muted">
              아직 대화가 없어요.
              <br />
              초록이에게 먼저 말을 걸어보세요 🌱
            </p>
          ) : (
            conversations.map((conversation) => {
              const isActive = conversation.id === activeId;
              return (
                <div
                  key={conversation.id}
                  className={`group flex items-center gap-1 rounded-xl pr-1 transition-colors ${
                    isActive ? 'bg-leaf-light' : 'hover:bg-surface'
                  }`}
                >
                  <button
                    type="button"
                    onClick={() => onSelect(conversation.id)}
                    aria-current={isActive ? 'true' : undefined}
                    className="min-w-0 flex-1 px-3 py-2 text-left"
                  >
                    <span
                      className={`block truncate text-sm ${
                        isActive ? 'font-semibold text-leaf-deep' : 'text-ink'
                      }`}
                    >
                      {conversation.title}
                    </span>
                    <span
                      className={`block text-[0.7rem] ${
                        isActive ? 'text-leaf-deep/70' : 'text-ink-muted'
                      }`}
                    >
                      {formatRelative(conversation.updatedAt)}
                    </span>
                  </button>

                  <button
                    type="button"
                    onClick={() => onExport(conversation.id)}
                    aria-label={`${conversation.title} 대화 내보내기`}
                    title="텍스트로 내보내기"
                    className={`shrink-0 rounded-lg p-1.5 transition-opacity hover:bg-surface-alt md:opacity-0 md:group-hover:opacity-100 ${
                      isActive ? 'text-leaf-deep md:opacity-100' : 'text-ink-muted'
                    }`}
                  >
                    <svg viewBox="0 0 20 20" className="h-3.5 w-3.5 fill-current" aria-hidden="true">
                      <path d="M9.1 2.2h1.8v7.5l2.4-2.4 1.3 1.3-4.6 4.6-4.6-4.6 1.3-1.3 2.4 2.4V2.2ZM3.2 14.1H5v2.1h10v-2.1h1.8v3.9H3.2v-3.9Z" />
                    </svg>
                  </button>

                  <button
                    type="button"
                    onClick={() => handleDelete(conversation)}
                    aria-label={`${conversation.title} 대화 삭제`}
                    title="대화 삭제"
                    className="shrink-0 rounded-lg p-1.5 text-ink-muted transition-opacity hover:bg-surface-alt hover:text-red-500 md:opacity-0 md:group-hover:opacity-100"
                  >
                    <svg viewBox="0 0 20 20" className="h-3.5 w-3.5 fill-current" aria-hidden="true">
                      <path d="M7.6 1.9h4.8l.7 1.4h3.4v1.8H3.5V3.3h3.4l.7-1.4ZM4.6 6.6h10.8l-.8 10.1a1.4 1.4 0 0 1-1.4 1.3H6.8a1.4 1.4 0 0 1-1.4-1.3L4.6 6.6Zm3 2v7.1h1.6V8.6H7.6Zm3.2 0v7.1h1.6V8.6h-1.6Z" />
                    </svg>
                  </button>
                </div>
              );
            })
          )}
        </nav>

        <p className="border-t border-edge px-4 py-3 text-[0.7rem] leading-relaxed text-ink-muted">
          대화는 이 브라우저에만 저장돼요.
        </p>
      </aside>
    </>
  );
}
