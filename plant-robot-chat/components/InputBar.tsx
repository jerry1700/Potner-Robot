'use client';

import { useEffect, useRef, useState, type KeyboardEvent } from 'react';

import { MAX_INPUT_CHARS } from '@/lib/constants';

/** textarea가 화면을 다 먹지 않도록 상한을 두고 그 뒤로는 내부 스크롤. */
const MAX_TEXTAREA_PX = 160;

/** 이 비율을 넘으면 글자 수를 경고색으로 보여준다. */
const WARN_RATIO = 0.9;

export interface InputBarProps {
  onSend: (text: string) => void;
  onStop?: () => void;
  isStreaming?: boolean;
  disabled?: boolean;
}

/**
 * 바깥 테두리·배경·최대폭은 부모(ChatWindow의 푸터)가 잡는다.
 * 여기서 또 border-t/max-w를 주면 선이 두 겹으로 겹쳐 보인다.
 */
export function InputBar({ onSend, onStop, isStreaming = false, disabled = false }: InputBarProps) {
  const [value, setValue] = useState('');
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const length = value.length;
  const isOverLimit = length > MAX_INPUT_CHARS;
  const isNearLimit = !isOverLimit && length >= MAX_INPUT_CHARS * WARN_RATIO;
  const canSend = value.trim().length > 0 && !isOverLimit && !disabled && !isStreaming;

  // 내용에 맞춰 높이 재계산. auto로 되돌리지 않으면 줄을 지워도 높이가 줄지 않는다.
  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = `${Math.min(el.scrollHeight, MAX_TEXTAREA_PX)}px`;
  }, [value]);

  function submit() {
    if (!canSend) return;
    onSend(value.trim());
    setValue('');
  }

  function handleKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key !== 'Enter' || event.shiftKey) return;
    // 한글은 조합 중에도 Enter가 올라온다. 조합 확정 Enter를 전송으로 삼으면 마지막 글자가 잘려 나간다.
    if (event.nativeEvent.isComposing) return;
    event.preventDefault();
    submit();
  }

  const counterTone = isOverLimit
    ? 'text-red-500'
    : isNearLimit
      ? 'text-amber-600 dark:text-amber-400'
      : 'text-ink-muted';

  return (
    <div>
      <div
        className={`flex items-end gap-2 rounded-2xl border bg-canvas px-2 py-2 transition-colors ${
          isOverLimit ? 'border-red-400' : 'border-edge focus-within:border-leaf'
        }`}
      >
        <button
          type="button"
          disabled
          title="파일 첨부는 다음 업데이트에서 지원될 예정이에요"
          aria-label="파일 첨부 (준비 중)"
          className="mb-0.5 shrink-0 cursor-not-allowed rounded-xl p-2 text-ink-muted opacity-40"
        >
          <svg viewBox="0 0 20 20" className="h-5 w-5 fill-current" aria-hidden="true">
            <path d="M13.3 3.4a3.9 3.9 0 0 1 5.5 5.5l-7.4 7.4a2.6 2.6 0 0 1-3.7-3.7l6.8-6.8 1.1 1.1-6.8 6.8a1 1 0 0 0 1.4 1.4l7.4-7.4a2.3 2.3 0 0 0-3.2-3.2L5.6 12.6a4.2 4.2 0 0 0 6 6l5.1-5.1 1.1 1.1-5.1 5.1a5.8 5.8 0 0 1-8.2-8.2l8.8-8.1Z" />
          </svg>
        </button>

        <label htmlFor="chat-input" className="sr-only">
          초록이에게 보낼 메시지
        </label>
        <textarea
          id="chat-input"
          ref={textareaRef}
          value={value}
          onChange={(event) => setValue(event.target.value)}
          onKeyDown={handleKeyDown}
          disabled={disabled}
          rows={1}
          placeholder="초록이에게 식물 이야기를 들려주세요 (Shift+Enter 줄바꿈)"
          aria-describedby="chat-input-hint"
          className="min-h-[2.5rem] w-full flex-1 resize-none bg-transparent py-2 text-sm leading-relaxed text-ink outline-none placeholder:text-ink-muted disabled:opacity-50"
        />

        {isStreaming ? (
          <button
            type="button"
            onClick={onStop}
            aria-label="응답 중단"
            title="응답 중단"
            className="mb-0.5 shrink-0 rounded-xl bg-red-500 p-2.5 text-white transition-colors hover:bg-red-600"
          >
            <svg viewBox="0 0 20 20" className="h-4 w-4 fill-current" aria-hidden="true">
              <rect x="5" y="5" width="10" height="10" rx="1.5" />
            </svg>
          </button>
        ) : (
          <button
            type="button"
            onClick={submit}
            disabled={!canSend}
            aria-label="메시지 보내기"
            title="메시지 보내기"
            className="mb-0.5 shrink-0 rounded-xl bg-leaf p-2.5 text-white transition-colors hover:bg-leaf-deep disabled:cursor-not-allowed disabled:opacity-40"
          >
            <svg viewBox="0 0 20 20" className="h-4 w-4 fill-current" aria-hidden="true">
              <path d="M10 2.6 17.4 10l-1.5 1.5-5-5V17.4h-1.8V6.5l-5 5L2.6 10 10 2.6Z" />
            </svg>
          </button>
        )}
      </div>

      <div className="mt-1.5 flex items-center justify-between gap-3 px-1 text-[0.7rem]">
        <span id="chat-input-hint" className={isOverLimit ? 'text-red-500' : 'text-ink-muted'}>
          {isOverLimit ? `${MAX_INPUT_CHARS.toLocaleString('ko-KR')}자까지만 보낼 수 있어요` : 'Enter로 전송'}
        </span>
        {length > 0 && (
          <span className={`shrink-0 tabular-nums ${counterTone}`}>
            {length.toLocaleString('ko-KR')} / {MAX_INPUT_CHARS.toLocaleString('ko-KR')}
          </span>
        )}
      </div>
    </div>
  );
}
