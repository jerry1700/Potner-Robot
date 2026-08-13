# 초록이 — 반려식물로봇 AI 대화 시스템

웹 브라우저에서 반려식물로봇 "초록이"와 대화하는 풀스택 앱.
Next.js 14 (App Router) + TypeScript + Tailwind CSS + Anthropic Claude API.

> 이 폴더는 **ROS 2 워크스페이스와 완전히 독립**입니다. `src/` 아래 ROS 패키지, `tests/`,
> Jenkins CI(파이썬 전용)를 전혀 건드리지 않습니다. `COLCON_IGNORE` 파일이 있어
> `colcon build` 도 이 폴더를 무시합니다.

---

## 1. 설치 및 실행

### 사전 요구사항: Node.js

Node.js가 없는 PC라면 먼저 설치하세요 (LTS 20 이상 권장):

```powershell
# winget 사용
winget install OpenJS.NodeJS.LTS

# 또는 https://nodejs.org 에서 LTS 설치 후 터미널 재시작
node --version   # v20.x 이상 확인
```

### 실행

```bash
cd plant-robot-chat
npm install

cp .env.local.example .env.local
#   → .env.local 을 열어 ANTHROPIC_API_KEY 에 GMS 키를 붙여넣으세요
#     (저장소 루트 .env 의 OPENAI_API_KEY 값과 동일한 키입니다)

npm run dev
# http://localhost:3000
```

타입 검사만 따로 돌리려면 `npm run typecheck`.

---

## 2. API 키 설정 — GMS 경유가 기본값

`.env.local`:

```
ANTHROPIC_API_KEY=S15P12E104-xxxxxxxx-...          # GMS 키
ANTHROPIC_BASE_URL=https://gms.ssafy.io/gmsapi/api.anthropic.com
ANTHROPIC_MODEL=claude-opus-4-8
```

**GMS가 Anthropic API도 프록시한다는 것을 실제 호출로 확인했습니다.** 별도의 Anthropic 키
(`sk-ant-...`) 없이 기존 SSAFY GMS 키 하나로 동작합니다. 인증 헤더가 `x-api-key`라서
Anthropic SDK 기본 방식과 그대로 맞습니다.

Anthropic 직접 키가 따로 있다면 `ANTHROPIC_BASE_URL`을 지우고 `ANTHROPIC_MODEL=claude-opus-5`로
올리면 됩니다.

### GMS에서 쓸 수 있는 모델 (실측)

| 모델 | GMS |
|---|---|
| `claude-opus-4-8` | ✅ **기본값** |
| `claude-opus-4-7` | ✅ |
| `claude-opus-4-6` | ✅ |
| `claude-sonnet-4-6` | ✅ (더 빠르고 저렴) |
| `claude-opus-5`, `claude-sonnet-5` | ❌ `is not available in Model` |
| `claude-haiku-4-5`, `claude-*-4-5`, 3.x 계열 | ❌ |

---

## 3. 이 코드베이스에서 반드시 지켜야 할 3가지 (실측 근거)

원래 요청 스펙과 **다르게 구현한 부분**입니다. 이유가 있습니다.

### ① 모든 Claude 호출은 스트리밍이다

GMS 게이트웨이는 **비-스트리밍 응답 바디의 앞부분을 잘라서 보냅니다.** 실측:

```
status=200  transfer-encoding=chunked
len=332
first 80 bytes: b'nd_turn","stop_sequence":null,...'   ← JSON 중간부터 시작
--> JSONDecodeError: Expecting value: line 1 column 1
```

반면 `stream: true`는 완벽하게 동작합니다 (`text/event-stream`, 이벤트 정상).
그래서 제목 생성·요약처럼 "한 방 응답"이면 되는 곳조차 `lib/anthropic.ts`의
`streamText()`(= `messages.stream()` + `finalMessage()`)를 씁니다.
**`messages.create({ stream: false })`는 이 코드베이스에서 금지입니다.**

> 참고: `src/potner_llm`에서 겪었던 `JSONDecodeError: Extra data`도 같은 계열의
> 게이트웨이 버그입니다. 그쪽은 재시도로 우회해 뒀습니다.

### ② `temperature`를 보내지 않는다

스펙에는 `temperature: 0.7`이 있었지만, **`claude-opus-4-7`/`4-8`은 `temperature`·`top_p`·`top_k`를
받으면 400을 냅니다.** 넣는 순간 앱 전체가 죽습니다. 그래서 아예 보내지 않고, 말투·응답 길이는
시스템 프롬프트의 `응답 길이` 섹션으로 제어합니다.

### ③ 모델 ID는 스펙의 값이 아니다

스펙의 `claude-sonnet-4-6-20250514`는 존재하지 않는 ID입니다 (모델 별칭에 날짜 suffix를 붙이면 404).
`claude-opus-4-8`을 기본값으로 하고 `ANTHROPIC_MODEL`로 바꿀 수 있게 했습니다.

---

## 4. 프로젝트 구조

```
plant-robot-chat/
├── app/
│   ├── layout.tsx              루트 레이아웃 (metadata, globals.css)
│   ├── page.tsx                메인 페이지
│   ├── globals.css             CSS 변수 팔레트 + 다크모드 + highlight.js
│   └── api/
│       ├── chat/route.ts       ★ 핵심: SSE 스트리밍 + 도구 호출 루프 + 안전 필터
│       ├── title/route.ts      대화 제목 자동 생성
│       └── summarize/route.ts  오래된 대화 요약 (토큰 초과 방지)
├── components/
│   ├── ChatWindow.tsx          채팅 화면 전체 조립
│   ├── MessageBubble.tsx       말풍선 + 마크다운 + 도구 결과 접이식 카드
│   ├── InputBar.tsx            입력창 (Enter 전송, 한글 IME 안전, 중단 버튼)
│   ├── Sidebar.tsx             대화 목록 / 새 대화 / 내보내기 / 삭제
│   ├── TypingIndicator.tsx     타이핑 애니메이션
│   ├── PlantAvatar.tsx         새싹 SVG 아바타
│   ├── SafetyNotice.tsx        안전 경고 UI
│   └── Toast.tsx               에러/알림 토스트
├── lib/
│   ├── config.ts               환경변수·상수 (서버 전용)
│   ├── anthropic.ts            SDK 클라이언트 + streamText (서버 전용)
│   ├── system-prompt.ts        초록이 페르소나 프롬프트
│   ├── plant-data.ts           실내식물 50종 관리 데이터
│   ├── tools.ts                도구 정의 5개 (Anthropic function calling)
│   ├── tool-handlers.ts        도구 실행 핸들러 (서버 전용)
│   ├── safety-filter.ts        입력/출력 안전 필터
│   ├── rate-limit.ts           분당 요청 제한
│   ├── conversation-manager.ts 대화 저장/불러오기/요약 분할 (localStorage)
│   └── use-chat.ts             클라이언트 상태 훅 (SSE 파싱)
├── types/chat.ts               ★ 공유 타입 계약
├── .env.local.example
└── COLCON_IGNORE               colcon이 이 폴더를 스캔하지 않게 하는 표식
```

---

## 5. 요청 처리 흐름

```
사용자 입력
  ↓  origin 검증 (CSRF) → rate limit (분당 20회)
  ↓  입력 안전 필터 (인젝션 / 자해 / 개인정보 / 민감 주제)
  ↓    └ 차단 시: 모델 호출 없이 초록이의 대체 응답을 SSE로 그대로 흘려보냄
  ↓  시스템 프롬프트 + 요약 + 최근 20턴 조립
  ↓  Claude 스트리밍 호출 (tools 포함)
  ↓    ├ text_delta → 즉시 프론트로 SSE 전송
  ↓    └ stop_reason=tool_use → 도구 실행 → tool_result 되돌림 → 반복 (최대 5회)
  ↓  출력 필터 (의료 면책 문구 / 2000자 절단)
  ↓  message_complete (토큰 사용량 + 도구 호출 기록)
```

### SSE 이벤트

```
event: text_delta        data: {"text":"안녕"}
event: tool_use_start    data: {"tool":"search_plant_info","status":"🔍 식물 정보를 찾아보고 있어요..."}
event: tool_use_end      data: {"tool":"search_plant_info","status":"완료"}
event: message_complete  data: {"usage":{...},"toolUse":[...]}
event: error             data: {"message":"잠시 문제가 생겼어요"}
```

---

## 6. 도구 (function calling)

| 도구 | 하는 일 |
|---|---|
| `search_plant_info` | 식물 50종 내장 DB에서 학명·물주기·빛·온습도·비료·병해충·독성·난이도 조회 |
| `diagnose_plant_condition` | 증상 키워드 → 가능한 원인 + 해결책 (잎 변색/처짐, 반점, 성장 멈춤, 줄기 연화) |
| `get_seasonal_care_tips` | 월 × 식물유형 관리 팁 매트릭스 |
| `recommend_plant` | 빛 환경·경험 수준·반려동물 유무·선호로 3~5종 추천 (독성 식물 자동 제외) |
| `get_current_weather` | Open-Meteo (API 키 불필요) 실시간 기온·습도 → 맞춤 조언 |

---

## 7. 안전 가드레일

**입력** — 프롬프트 인젝션(시스템 프롬프트 유출 없이 자연스럽게 전환), 자해·폭력·불법,
주민번호·카드번호(대화에 저장하지 않음), 정치·종교.

자해 암시 시 공감 + 전문 상담 안내: **자살예방상담전화 1393**, **정신건강위기상담전화 1577-0199**.

> 식물 앱 특성상 "식물이 죽고 있어", "다 죽었어" 같은 표현이 자주 나옵니다.
> 자해 감지 정규식이 이런 식물 문맥을 오탐하지 않도록 예외 처리해 뒀습니다.

**출력** — 의약품/복용량/진단명 패턴 감지 시 "전문의와 상담하세요" 면책 문구 자동 추가,
2000자 초과 시 문장 경계에서 자연스럽게 절단.

---

## 8. 알려진 한계

- **빌드/실행 검증 안 됨** — 이 PC에 Node.js가 없어 `npm install`·`npm run dev`·`tsc`를
  돌려보지 못했습니다. 코드는 완성됐고 모듈 간 계약을 교차 검증했지만, 첫 `npm run dev`에서
  의존성 버전 관련 조정이 필요할 수 있습니다.
- **대화 저장은 localStorage** — 브라우저별로 분리되고 서버에 남지 않습니다.
  여러 기기에서 공유하려면 백엔드 저장이 필요합니다.
- **rate limit은 프로세스 메모리** — 서버 재시작 시 초기화되고 인스턴스 간 공유되지 않습니다.
- **이미지 업로드 미구현** — 입력창의 파일 첨부 버튼은 비활성 상태입니다.
  Claude는 vision을 지원하므로 확장 시 `image` 콘텐츠 블록을 추가하면 됩니다.
- **안전 필터는 정규식 1차 판정만** — 스펙의 "의심 입력 2차 LLM 판정"은 레이턴시·비용 때문에
  넣지 않았습니다. 필요하면 `lib/safety-filter.ts`에 비동기 경로를 추가하면 됩니다.

---

## 9. 로봇(ROS 2) 쪽과의 관계

이 웹앱은 독립 실행됩니다. `src/potner_llm`(젯슨에서 도는 ROS 2 LLM 패키지)과는 별개이고,
공유하는 것은 GMS 키뿐입니다. 나중에 둘을 잇는다면 자연스러운 접점은
`potner_bridge`(MQTT ↔ ROS 2)입니다 — 실제 센서 상태를 MQTT로 받아 이 앱의 도구로
노출하면 초록이가 "지금 우리 집 화분" 상태를 근거로 말할 수 있습니다.
