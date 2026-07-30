/** 잎이 튀는 점 3개. 지연을 다르게 줘서 순차적으로 튀어 보이게 한다. */
const DOT_DELAYS = ['0ms', '160ms', '320ms'];

/**
 * 아바타는 그리지 않는다 — 호출하는 쪽(ChatWindow)이 말풍선 왼쪽에 이미 아바타를 세우므로
 * 여기서 또 그리면 새싹이 두 개로 보인다.
 */
export function TypingIndicator({ label = '초록이가 생각하는 중...' }: { label?: string }) {
  return (
    <div
      className="flex w-fit animate-fade-up items-center gap-2 rounded-2xl rounded-tl-md border border-edge bg-surface px-3.5 py-2.5"
      role="status"
      aria-live="polite"
    >
      <span className="flex items-end gap-1" aria-hidden="true">
        {DOT_DELAYS.map((delay) => (
          <span
            key={delay}
            className="h-1.5 w-1.5 animate-leaf-bounce rounded-full bg-leaf"
            style={{ animationDelay: delay }}
          />
        ))}
      </span>
      <span className="text-xs text-ink-muted">{label}</span>
    </div>
  );
}
