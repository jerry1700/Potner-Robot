'use client';

import { useEffect } from 'react';

/** 자동 사라짐까지의 시간. 한국어 한 문장을 읽기에 충분한 정도. */
const AUTO_DISMISS_MS = 4000;

export function Toast({ message, onDismiss }: { message: string; onDismiss: () => void }) {
  // 메시지가 바뀌면 타이머를 다시 시작한다. 언마운트 시 clear해야 이미 사라진 토스트가 상태를 건드리지 않는다.
  useEffect(() => {
    const timer = setTimeout(onDismiss, AUTO_DISMISS_MS);
    return () => clearTimeout(timer);
  }, [message, onDismiss]);

  return (
    <div
      role="status"
      aria-live="polite"
      className="pointer-events-none fixed bottom-24 left-1/2 z-50 flex w-[min(92vw,26rem)] -translate-x-1/2 justify-center"
    >
      <div className="pointer-events-auto flex animate-toast-in items-start gap-3 rounded-2xl border border-edge bg-surface px-4 py-3 shadow-lg">
        <p className="min-w-0 flex-1 break-words text-sm leading-relaxed text-ink">{message}</p>
        <button
          type="button"
          onClick={onDismiss}
          aria-label="알림 닫기"
          className="-mr-1 -mt-0.5 shrink-0 rounded-lg p-1 text-ink-muted transition-colors hover:bg-surface-alt hover:text-ink"
        >
          <svg viewBox="0 0 20 20" className="h-4 w-4 fill-current" aria-hidden="true">
            <path d="M5.5 4.4 10 8.9l4.5-4.5 1.1 1.1L11.1 10l4.5 4.5-1.1 1.1L10 11.1l-4.5 4.5-1.1-1.1L8.9 10 4.4 5.5 5.5 4.4Z" />
          </svg>
        </button>
      </div>
    </div>
  );
}
