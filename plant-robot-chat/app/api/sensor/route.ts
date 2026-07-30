/**
 * POST /api/sensor — 로봇(하드웨어)이 최신 센서 값을 밀어 넣는 엔드포인트.
 * GET /api/sensor — 현재 캐시된 최신 값 확인용(디버깅·상태 표시).
 *
 * 브라우저가 아니라 로봇 프로세스가 호출하므로 isAllowedOrigin(origin/host 검사)이 아니라
 * 공유 비밀키(X-Sensor-Key 헤더)로 인증한다.
 */

import { getSensorIngestKey } from '@/lib/config';
import { getSensorReading, isSensorReadingStale, setSensorReading } from '@/lib/sensor-store';
import type { SensorIngestBody, SensorReading } from '@/types/chat';

export const runtime = 'nodejs';

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json; charset=utf-8' },
  });
}

/** 인증 실패 시 키 존재 여부까지 노출하지 않도록 메시지를 하나로 통일한다. */
function isAuthorized(req: Request): boolean {
  const expected = getSensorIngestKey();
  if (!expected) return false; // 키 미설정이면 누구도 밀어 넣을 수 없다(오픈 엔드포인트 방지).
  const provided = req.headers.get('x-sensor-key');
  return provided === expected;
}

function asFiniteNumber(value: unknown): number | undefined {
  return typeof value === 'number' && Number.isFinite(value) ? value : undefined;
}

function parseIngestBody(raw: unknown): SensorIngestBody {
  if (typeof raw !== 'object' || raw === null) return {};
  const body = raw as Partial<Record<keyof SensorIngestBody, unknown>>;
  return {
    soilMoisturePercent: asFiniteNumber(body.soilMoisturePercent),
    temperatureCelsius: asFiniteNumber(body.temperatureCelsius),
    humidityPercent: asFiniteNumber(body.humidityPercent),
    lightLux: asFiniteNumber(body.lightLux),
  };
}

export async function POST(req: Request): Promise<Response> {
  if (!isAuthorized(req)) {
    return jsonResponse({ error: '인증에 실패했습니다' }, 401);
  }

  const raw: unknown = await req.json().catch(() => null);
  const parsed = parseIngestBody(raw);

  const hasAnyValue =
    parsed.soilMoisturePercent !== undefined ||
    parsed.temperatureCelsius !== undefined ||
    parsed.humidityPercent !== undefined ||
    parsed.lightLux !== undefined;

  if (!hasAnyValue) {
    return jsonResponse({ error: '유효한 센서 값이 없습니다' }, 400);
  }

  const reading: SensorReading = { ...parsed, measuredAt: new Date().toISOString() };
  setSensorReading(reading);

  return jsonResponse({ ok: true, reading });
}

export async function GET(req: Request): Promise<Response> {
  if (!isAuthorized(req)) {
    return jsonResponse({ error: '인증에 실패했습니다' }, 401);
  }

  const reading = getSensorReading();
  if (!reading) {
    return jsonResponse({ reading: null });
  }

  return jsonResponse({ reading, stale: isSensorReadingStale(reading, new Date()) });
}
