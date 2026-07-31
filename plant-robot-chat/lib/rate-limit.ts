/**
 * 메모리 기반 슬라이딩 윈도우 rate limit.
 *
 * ★ 프로세스 단위 상태다 — 재배포/서버 재시작/인스턴스 추가 시 카운터가 초기화된다.
 *   단일 인스턴스 개발용 앱에는 충분하고, 여러 인스턴스로 확장하면 Redis 같은 공용 저장소로 옮겨야 한다.
 */

import { RATE_LIMIT_PER_MINUTE } from '@/lib/config';

const WINDOW_MS = 60_000;

/** 전체 정리 최소 간격 — 매 요청마다 Map 전체를 훑지 않기 위해 */
const SWEEP_INTERVAL_MS = 60_000;

/** 키가 무한히 늘어나는 것을 막는 상한 (넘으면 즉시 정리, 그래도 넘치면 가장 오래된 키부터 버린다) */
const MAX_KEYS = 5_000;

/** key → 윈도우 안의 요청 시각(ms). 항상 오름차순으로 유지된다. */
const buckets = new Map<string, number[]>();
let lastSweep = 0;

/** 윈도우를 벗어난 시각을 걷어내고, 남은 게 없으면 키 자체를 지운다 */
function sweep(now: number): void {
  const cutoff = now - WINDOW_MS;
  for (const [key, times] of buckets) {
    const alive = times.filter((t) => t > cutoff);
    if (alive.length === 0) buckets.delete(key);
    else buckets.set(key, alive);
  }
  lastSweep = now;
}

export function checkRateLimit(key: string): { allowed: boolean; retryAfterSeconds?: number } {
  const now = Date.now();

  if (now - lastSweep > SWEEP_INTERVAL_MS || buckets.size > MAX_KEYS) {
    sweep(now);
    // 정리 후에도 넘치면 삽입 순서(= 오래된 키) 기준으로 잘라낸다
    while (buckets.size > MAX_KEYS) {
      const oldest = buckets.keys().next();
      if (oldest.done) break;
      buckets.delete(oldest.value);
    }
  }

  const cutoff = now - WINDOW_MS;
  const times = (buckets.get(key) ?? []).filter((t) => t > cutoff);

  if (times.length >= RATE_LIMIT_PER_MINUTE) {
    buckets.set(key, times);
    // 가장 오래된 요청이 윈도우에서 빠져나가는 시점까지 기다려야 한다
    const retryAfterSeconds = Math.max(1, Math.ceil((times[0] + WINDOW_MS - now) / 1000));
    return { allowed: false, retryAfterSeconds };
  }

  times.push(now);
  buckets.set(key, times);
  return { allowed: true };
}
