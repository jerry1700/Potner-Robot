import { memo } from 'react';
import ReactMarkdown from 'react-markdown';
import type { Components } from 'react-markdown';
import remarkGfm from 'remark-gfm';
import rehypeHighlight from 'rehype-highlight';

import { PlantAvatar } from '@/components/PlantAvatar';
import type { Message, ToolInvocation } from '@/types/chat';

/**
 * Tailwind preflight가 list-style과 여백을 모두 리셋하므로, 마크다운 목록/표는
 * 여기서 직접 클래스를 복원하지 않으면 평문처럼 뭉개져 보인다.
 * (`node`는 DOM 속성이 아니라서 반드시 분리해 버려야 React가 경고하지 않는다.)
 */
const markdownComponents: Components = {
  p: ({ node, ...props }) => <p className="leading-relaxed" {...props} />,
  a: ({ node, ...props }) => (
    // 외부 링크는 대화를 잃지 않게 새 탭으로. noreferrer로 리퍼러 노출 차단.
    <a
      className="font-medium text-leaf-deep underline decoration-leaf/50 underline-offset-2 hover:decoration-leaf"
      target="_blank"
      rel="noreferrer"
      {...props}
    />
  ),
  ul: ({ node, ...props }) => <ul className="list-disc space-y-1 pl-5 marker:text-leaf" {...props} />,
  ol: ({ node, ...props }) => (
    <ol className="list-decimal space-y-1 pl-5 marker:text-leaf" {...props} />
  ),
  li: ({ node, ...props }) => <li className="leading-relaxed [&>ol]:mt-1 [&>ul]:mt-1" {...props} />,
  h1: ({ node, ...props }) => <h1 className="mt-1 text-base font-bold text-ink" {...props} />,
  h2: ({ node, ...props }) => <h2 className="mt-1 text-base font-bold text-ink" {...props} />,
  h3: ({ node, ...props }) => <h3 className="mt-1 text-sm font-bold text-ink" {...props} />,
  strong: ({ node, ...props }) => <strong className="font-semibold text-ink" {...props} />,
  blockquote: ({ node, ...props }) => (
    <blockquote className="border-l-2 border-leaf/60 pl-3 text-ink-muted" {...props} />
  ),
  hr: ({ node, ...props }) => <hr className="border-edge" {...props} />,
  pre: ({ node, ...props }) => (
    // 긴 코드는 버블을 밀어내지 않고 자기 안에서 가로 스크롤되게 한다.
    <pre
      className="overflow-x-auto rounded-xl border border-edge bg-surface-alt p-3 text-xs leading-relaxed"
      {...props}
    />
  ),
  code: ({ node, className, children, ...props }) => {
    // rehype-highlight가 붙인 language-* / hljs 클래스로 블록인지 인라인인지 가른다.
    const isBlock =
      typeof className === 'string' && (className.includes('language-') || className.includes('hljs'));
    return (
      <code
        className={
          isBlock
            ? `${className} font-mono`
            : 'rounded bg-surface-alt px-1.5 py-0.5 font-mono text-[0.85em] text-leaf-deep'
        }
        {...props}
      >
        {children}
      </code>
    );
  },
  table: ({ node, ...props }) => (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse text-left text-xs" {...props} />
    </div>
  ),
  thead: ({ node, ...props }) => <thead className="bg-surface-alt" {...props} />,
  th: ({ node, ...props }) => (
    <th className="border border-edge px-2.5 py-1.5 font-semibold text-ink" {...props} />
  ),
  td: ({ node, ...props }) => (
    <td className="border border-edge px-2.5 py-1.5 align-top" {...props} />
  ),
};

/** 도구 이름을 그대로 노출하면 사용자가 읽을 수 없어 한국어 라벨을 앞세운다. */
const TOOL_LABELS: Record<string, string> = {
  search_plant_info: '식물 정보 검색',
  diagnose_plant_condition: '증상 진단',
  get_seasonal_care_tips: '계절별 관리 팁',
  recommend_plant: '식물 추천',
  get_current_weather: '현재 날씨',
  search_web_knowledge: '위키백과 검색',
  get_news: '오늘의 뉴스',
  get_plant_sensor_status: '센서 상태',
};

/** 도구 결과는 JSON 문자열로 들어오지만 실패 시 평문일 수 있어 파싱 실패를 허용한다. */
function prettyJson(value: unknown): string {
  if (value === undefined || value === null) return '(없음)';
  if (typeof value === 'string') {
    try {
      return JSON.stringify(JSON.parse(value), null, 2);
    } catch {
      return value;
    }
  }
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

/** hour12를 끄지 않으면 ko-KR이 "오후 03:24"로 늘어난다 — 말풍선 밑에는 HH:MM만 필요하다. */
function formatTime(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return '';
  return date.toLocaleTimeString('ko-KR', { hour: '2-digit', minute: '2-digit', hour12: false });
}

function ToolCard({ tool }: { tool: ToolInvocation }) {
  const label = TOOL_LABELS[tool.name] ?? tool.name;

  return (
    <details className="group rounded-xl border border-edge bg-surface-alt">
      <summary className="flex cursor-pointer select-none items-center gap-2 px-3 py-2 text-xs text-ink-muted">
        <svg viewBox="0 0 20 20" className="h-3 w-3 shrink-0 fill-current" aria-hidden="true">
          <path d="M12.8 1.6a5.6 5.6 0 0 0-5.2 7.7L2 14.9V18h3.1l5.6-5.6a5.6 5.6 0 0 0 6.7-7.2l-2.8 2.8-2.3-.6-.6-2.3 2.8-2.8a5.6 5.6 0 0 0-1.7-.7Z" />
        </svg>
        <span className="font-medium text-ink">{label}</span>
        <span className={tool.isError ? 'text-amber-600 dark:text-amber-400' : 'text-leaf-deep'}>
          {tool.isError ? '실패' : '완료'}
        </span>
        <span className="ml-auto text-[0.7rem] opacity-70 group-open:hidden">자세히</span>
      </summary>
      <div className="space-y-2 border-t border-edge px-3 py-2.5">
        <p className="text-[0.7rem] font-semibold tracking-wide text-ink-muted">
          입력 <code className="font-mono opacity-70">{tool.name}</code>
        </p>
        <pre className="max-h-40 overflow-auto rounded-lg bg-surface p-2 font-mono text-[0.7rem] leading-relaxed text-ink">
          {prettyJson(tool.input)}
        </pre>
        <p className="text-[0.7rem] font-semibold tracking-wide text-ink-muted">결과</p>
        <pre className="max-h-60 overflow-auto rounded-lg bg-surface p-2 font-mono text-[0.7rem] leading-relaxed text-ink">
          {prettyJson(tool.result)}
        </pre>
      </div>
    </details>
  );
}

function MessageBubbleBase({ message }: { message: Message }) {
  const isUser = message.role === 'user';
  const time = formatTime(message.timestamp);

  if (isUser) {
    return (
      <div className="flex animate-fade-up justify-end">
        <div className="flex max-w-[80%] flex-col items-end gap-1">
          <div className="whitespace-pre-wrap break-words rounded-2xl rounded-tr-md bg-leaf-light px-4 py-2.5 text-sm leading-relaxed text-leaf-deep">
            <span className="sr-only">나: </span>
            {message.content}
          </div>
          {time !== '' && (
            // 서버 렌더 시각대와 브라우저 시각대가 다를 수 있어 경고만 억제한다 (값은 클라이언트 기준이 정답)
            <time
              dateTime={message.timestamp}
              suppressHydrationWarning
              className="px-1 text-[0.7rem] text-ink-muted"
            >
              {time}
            </time>
          )}
        </div>
      </div>
    );
  }

  // 안전 필터가 만든 답변은 초록이의 평소 말풍선과 확실히 구분돼야 한다
  const bubbleTone = message.safetyBlocked
    ? 'border-dashed border-amber-300 bg-amber-50 text-amber-900 dark:border-amber-500/40 dark:bg-amber-500/10 dark:text-amber-100'
    : 'border-edge bg-surface text-ink';

  return (
    <div className="flex animate-fade-up gap-2.5">
      <PlantAvatar size={30} />
      <div className="flex min-w-0 max-w-[85%] flex-col gap-1">
        {/* sr-only는 absolute라 흐름에서 빠진다 — 말풍선 안에 두면 space-y가 첫 줄을 밀어낸다 */}
        <span className="sr-only">초록이: </span>
        <div
          className={`min-w-0 space-y-3 rounded-2xl rounded-tl-md border px-4 py-3 text-sm ${bubbleTone}`}
        >
          {message.safetyBlocked && (
            <p className="text-[0.7rem] font-semibold tracking-wide text-amber-700 dark:text-amber-300">
              안전 안내
            </p>
          )}
          <ReactMarkdown
            remarkPlugins={[remarkGfm]}
            rehypePlugins={[rehypeHighlight]}
            components={markdownComponents}
          >
            {message.content}
          </ReactMarkdown>
          {message.isStreaming && (
            <span className="ml-0.5 inline-block h-3.5 w-1.5 animate-leaf-bounce rounded-sm bg-leaf align-middle" />
          )}
        </div>

        {message.toolUse !== undefined && message.toolUse.length > 0 && (
          <div className="space-y-1.5">
            {message.toolUse.map((tool) => (
              <ToolCard key={tool.id} tool={tool} />
            ))}
          </div>
        )}

        {time !== '' && (
          <time
            dateTime={message.timestamp}
            suppressHydrationWarning
            className="px-1 text-[0.7rem] text-ink-muted"
          >
            {time}
          </time>
        )}
      </div>
    </div>
  );
}

/**
 * 스트리밍 토큰 1개마다 conversations 배열이 새로 커밋돼 ChatWindow가 통째로 리렌더된다.
 * memo가 없으면 대화의 모든 말풍선이 매 토큰마다 마크다운을 다시 파싱해 메인 스레드가 막힌다.
 * (patchAssistant가 수정되지 않은 message 객체의 참조를 그대로 유지하므로 얕은 비교로 충분하다.)
 */
export const MessageBubble = memo(MessageBubbleBase);
