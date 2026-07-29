# 진행 상태 (2026-07-29 갱신)

## 완료된 것

- ✅ 소스 26개 파일 작성 (2026-07-28)
- ✅ `lib/constants.ts` 분리 — 클라이언트 안전 상수 4개 이동, `lib/config.ts`는 re-export,
  `InputBar.tsx`/`conversation-manager.ts`는 `@/lib/constants`에서 import (2026-07-29)
- ✅ `npm install` + `npm run typecheck` — **타입 에러 0개** (첫 진짜 검증 통과)
- ✅ `.env.local` 생성 (루트 `.env`의 GMS 키 사용)
- ✅ 서버 API 전부 실호출 검증 (GMS 게이트웨이 경유, 전부 200):
  - `/api/chat` 일반 대화 — 스트리밍 델타 정상
  - `/api/chat` "몬스테라 물주기" — `search_plant_info` 도구 루프 정상
  - `/api/chat` 추천 멀티턴 — 모델이 환경을 되물은 뒤 `recommend_plant` 호출, 고양이 안전 필터 반영
  - `/api/title` — "몬스테라 물주기" 생성 (요청 필드는 `firstUserMessage`)
  - `/api/summarize` — 구조화된 요약 생성
- 어제 계획했던 에이전트 검증 워크플로우 재개는 **불필요 판정** — 컴파일러 + 실호출 검증으로 대체됨

## 남은 것 — 브라우저 수동 확인 (UI 체크리스트)

`npm run dev` 후 http://localhost:3000 에서:

- [ ] 대화가 스트리밍으로 한 글자씩 나오는지 (API는 확인됨, UI 렌더만 확인)
- [ ] 도구 호출 카드가 뜨는지
- [ ] 새 대화 / 사이드바 / 내보내기 / 제목 자동생성
- [ ] 다크모드 (OS 테마 전환)
- [ ] 한글 입력 중 Enter가 조합을 끊고 전송해버리지 않는지 (IME 가드)

## 절대 잊지 말 것 — 실측 API 제약 (GMS 게이트웨이)

1. **비-스트리밍 호출 금지** — 응답 바디 앞부분이 잘림. 항상 `messages.stream()` + `finalMessage()`.
2. **`temperature`/`top_p`/`top_k` 금지** — 400. 톤은 시스템 프롬프트로.
3. **모델 allowlist**: `claude-opus-4-8`(기본) / `4-7` / `4-6` / `claude-sonnet-4-6`.
   5 계열은 400.

```
ANTHROPIC_API_KEY=<루트 .env 의 OPENAI_API_KEY 와 같은 GMS 키>
ANTHROPIC_BASE_URL=https://gms.ssafy.io/gmsapi/api.anthropic.com
ANTHROPIC_MODEL=claude-opus-4-8
```

## 의도적으로 안 넣은 것 (스펙에 있지만 제외)

- **안전 필터 2차 LLM 판정** — 레이턴시·비용 2배라 정규식 1차만.
- **환각 방지 일관성 검사** — 오탐 위험.
- **이미지 업로드** — 버튼 비활성 상태로 자리만.
