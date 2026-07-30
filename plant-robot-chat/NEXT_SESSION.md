# 진행 상태 (2026-07-29 갱신 — 응답 검증 + 음성 대화 서버)

> 오늘 작업은 둘 다 **커밋 전** 상태다. 브랜치: `Robot-feature/implement-response-validation`

## 오늘 완료한 것 ①: LLM 응답 검증 파이프라인 (`src/potner_llm/`)

- 새 모듈 7개: `exceptions.py` `models.py` `config.py` `validator.py`
  `prompt_builder.py` `postprocessor.py` `service.py`
- 진입점 `PlantChatService.answer(질문)` → 예외 없이 항상 JSON dict 반환
  (입력 검증 → 프로필/센서 조회 → 동적 프롬프트 → LLM → 응답 검증 → 후처리)
- `client.py` 보강: timeout 설정(`OPENAI_TIMEOUT_SECONDS`) + 유형별 예외.
  모든 예외는 `LlmError(RuntimeError)` 상속 — **DialogueService의
  `except RuntimeError` 폴백과의 호환이 의도된 설계이니 바꾸지 말 것.**
- 테스트: `tests/test_llm_service.py` 포함 **137개 전부 통과**
  (`test_config_consistency`/`test_marker_pose` 2개 파일은 로컬에 cv2가 없어
  원래부터 수집 실패 — 이번 작업과 무관, 젠킨스에서는 문제없음)

```bash
python -m pytest tests/ -q --ignore=tests/test_config_consistency.py --ignore=tests/test_marker_pose.py
```

## 오늘 완료한 것 ②: 음성 대화 서버 (`voice-chat-server/`)

휴대폰 브라우저(같은 Wi-Fi)로 로봇과 음성 대화. **e2e 실측 통과** (턴당 ~8초).

- 체인: 🎤 MediaRecorder → `POST /api/voice-chat` → GMS whisper-1 STT
  → `potner_llm.DialogueService`(tool use·페르소나·히스토리 재사용)
  → GMS gpt-4o-mini-tts → mp3 자동 재생
- 파일: `app.py`(서버) / `speech.py`(STT·TTS) / `static/index.html`(웹페이지)
  / `README.md`(실행·휴대폰 테스트 가이드 — **꼭 먼저 읽기**)
- 스택 결정(사용자 확정): STT/TTS는 GMS 클라우드, LLM은 potner_llm 재사용

```bash
pip install -r voice-chat-server/requirements.txt   # 최초 1회
python -m uvicorn app:app --app-dir voice-chat-server --host 0.0.0.0 --port 8080
```

## 🔴 내일 해야 할 일

1. **커밋 정리** — 두 작업을 분리해서 커밋:
   - ①번: `feat(robot): LLM 응답 검증 파이프라인` (`src/potner_llm/` + `tests/test_llm_service.py`)
   - ②번: `feat(robot): 음성 대화 웹서버` (`voice-chat-server/`)
2. **휴대폰 실기기 테스트** — PC에서 서버 띄우고 폰으로 접속.
   ⚠️ `http://IP`는 마이크가 차단됨(보안 컨텍스트). Android는 Chrome 플래그
   `#unsafely-treat-insecure-origin-as-secure`, iPhone은 자체서명 HTTPS.
   절차는 `voice-chat-server/README.md`에 있음.
3. **실제 센서 연결** — `voice-chat-server/app.py`의 `_demo_status()`(고정: 토양 건조)를
   MQTT/ROS에서 최신 `PlantStatus`를 주는 provider로 교체.
4. (검토) **응답 검증을 음성 경로에도 적용** — 지금 음성 서버는 `DialogueService.chat_once`를
   써서 이번에 만든 `validator/postprocessor`를 안 탄다. `chat_once` 응답에
   `check_response`+`postprocess`를 끼우거나 서비스 계층을 통일할지 결정.
5. (여유되면) 웹 챗 브라우저 수동 확인 — 아래 이전 체크리스트 참고.

## 절대 잊지 말 것 — GMS 실측 제약

**음성 API (2026-07-29 실측):**
1. STT는 `whisper-1`만, TTS는 `gpt-4o-mini-tts`만 허용. 나머지는 400 차단.
2. **음성 API는 stdlib urllib 금지** — 응답 바디가 항상 유실됨(재시도 무관).
   반드시 `requests` 사용 (speech.py가 이미 그렇게 되어 있음).
3. `gpt-4o-mini-tts`는 `instructions`로 말투 조절 가능 (오린카 톤 적용됨).

**Anthropic 프록시 (웹 챗, 기존 실측):**
1. 비-스트리밍 호출 금지 — 바디 앞부분 잘림. 항상 `messages.stream()`.
2. `temperature`/`top_p`/`top_k` 금지 — 400. 톤은 시스템 프롬프트로.
3. 모델 allowlist: `claude-opus-4-8`(기본) / `4-7` / `4-6` / `claude-sonnet-4-6`.

```
ANTHROPIC_API_KEY=<루트 .env 의 GMS 키와 동일>
ANTHROPIC_BASE_URL=https://gms.ssafy.io/gmsapi/api.anthropic.com
ANTHROPIC_MODEL=claude-opus-4-8
```

---

## (이월) 웹 챗 plant-robot-chat — 남은 브라우저 수동 확인

API 실호출 검증은 전부 통과(2026-07-29). `npm run dev` 후 http://localhost:3000 에서:

- [ ] 대화가 스트리밍으로 한 글자씩 나오는지 (API는 확인됨, UI 렌더만 확인)
- [ ] 도구 호출 카드가 뜨는지
- [ ] 새 대화 / 사이드바 / 내보내기 / 제목 자동생성
- [ ] 다크모드 (OS 테마 전환)
- [ ] 한글 입력 중 Enter가 조합을 끊고 전송해버리지 않는지 (IME 가드)

의도적으로 제외한 것: 안전 필터 2차 LLM 판정(비용), 환각 일관성 검사(오탐),
이미지 업로드(버튼 자리만).
