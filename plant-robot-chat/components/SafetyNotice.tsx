import type { ReactNode } from 'react';

/**
 * 위기/안전 안내 문구 래퍼. 일반 답변과 확실히 구분돼야 사용자가 놓치지 않으므로
 * 앰버 톤 + 아이콘으로 시각적 무게를 준다.
 */
export function SafetyNotice({ children }: { children: ReactNode }) {
  return (
    <div
      role="note"
      className="flex gap-3 rounded-2xl border border-amber-300 bg-amber-50 px-4 py-3 text-sm leading-relaxed text-amber-900 dark:border-amber-500/40 dark:bg-amber-500/10 dark:text-amber-200"
    >
      <svg
        viewBox="0 0 20 20"
        className="mt-0.5 h-4 w-4 shrink-0 fill-current"
        aria-hidden="true"
      >
        <path d="M10 1.6a1.5 1.5 0 0 1 1.3.76l7.1 12.4A1.5 1.5 0 0 1 17.1 17H2.9a1.5 1.5 0 0 1-1.3-2.24l7.1-12.4A1.5 1.5 0 0 1 10 1.6Zm0 4.6a.9.9 0 0 0-.9.98l.32 3.7a.58.58 0 0 0 1.16 0l.32-3.7A.9.9 0 0 0 10 6.2Zm0 6.4a1 1 0 1 0 0 2 1 1 0 0 0 0-2Z" />
      </svg>
      <div className="min-w-0 space-y-1.5">{children}</div>
    </div>
  );
}
