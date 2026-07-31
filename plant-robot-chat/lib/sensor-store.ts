/**
 * 최신 센서 값 인메모리 캐시. 서버 전용, 프로세스 하나만 돌아간다는 전제(단일 로봇/단일
 * 배포)를 깐다 — 여러 인스턴스로 스케일하게 되면 Redis 등 외부 저장소로 옮겨야 한다.
 *
 * Next.js dev 서버는 파일이 바뀔 때마다 모듈을 다시 로드할 수 있어 모듈 스코프 변수가
 * 초기화될 수 있다. globalThis에 얹어 hot reload 사이에도 값이 유지되게 한다.
 */

import type { SensorReading } from '@/types/chat';

const KEY = '__potnerLatestSensorReading__';

interface GlobalWithSensor {
  [KEY]?: SensorReading;
}

const globalStore = globalThis as GlobalWithSensor;

/** 센서 값이 이보다 오래되면 도구가 "최근 값 없음"으로 취급한다. */
export const SENSOR_STALE_AFTER_MS = 30 * 60 * 1000;

export function setSensorReading(reading: SensorReading): void {
  globalStore[KEY] = reading;
}

export function getSensorReading(): SensorReading | null {
  return globalStore[KEY] ?? null;
}

export function isSensorReadingStale(reading: SensorReading, now: Date): boolean {
  const measuredAt = Date.parse(reading.measuredAt);
  if (!Number.isFinite(measuredAt)) return true;
  return now.getTime() - measuredAt > SENSOR_STALE_AFTER_MS;
}
