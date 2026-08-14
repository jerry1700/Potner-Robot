# CLAUDE.md — 로봇(Jetson) 트리 작업 안내

이 트리는 **젯슨 ROS2 로봇** 코드다 (colcon 워크스페이스 `src/potner_*` +
FastAPI 음성 서버 `voice-chat-server/`). 라즈베리파이 스테이션 코드는
`Raspberry-develop` 브랜치에 **완전히 다른 레이아웃**으로 따로 있다 —
브랜치 이름만 보고 트리를 단정하지 말 것 (`git ls-tree HEAD` 로 확인).

## 불변식 (깨면 안 되는 것)

- **테스트는 실제 GMS 엔드포인트를 절대 부르지 않는다.** 네트워크는 전부
  monkeypatch(`urllib.request.urlopen` / `requests` 대역)로 막는다. API
  키를 넣어 통과시키지 말고 mock을 고칠 것. 실호출 검증이 필요하면 임시
  스크립트로 하고 **실행 후 삭제**한다 (레포에 비-CI 테스트 파일을 남기지
  않는다).
- **스피커 명령은 인자 배열로만 조립한다** (`shell=True` 금지). 문장·파일
  경로가 서버 LLM이 만든 외부 문자열이기 때문이다 — `potner_base/speech.py`.
- **`speaker:` 파라미터는 노드와 yaml 두 곳을 함께 고친다.**
  `tests/test_config_consistency.py`가 `declare_parameter` 기본값과
  `potner_params.yaml`을 대조한다. 값이 틀리면 값을 고치지, 테스트를
  고치지 않는다.
- **`voice-chat-server/app.py`에 순수 로직을 넣지 않는다.** app.py는
  import 시점에 API 키를 요구하고 실 GMS 호출 스레드를 띄워서 테스트가
  import할 수 없다. 문장 처리 같은 순수 로직은 `sentences.py`처럼 별도
  모듈로 두고 app.py는 배선만 한다.
- GMS 음성 API(whisper/tts)는 **requests 필수** — stdlib urllib은 GMS
  프록시에서 응답 바디가 유실된다(실측). LLM chat API와 Spring 서버는
  urllib으로도 된다.

## 자주 쓰는 명령

```bash
python -m pytest tests/ -q                # 전체 (ROS 없이 돈다)
python -m pytest tests/test_llm*.py -q    # LLM만 (= llm-test 스킬)
python -m flake8 . --count --select=E9,F63,F7,F82 \
    --exclude=venv,.git,__pycache__,build,install,log,.pio   # CI 게이트
```

## 지도

| 위치 | 내용 |
|---|---|
| `src/potner_base` | 구동·센서·스피커(`speaker_node`)·표정 |
| `src/potner_bridge` | MQTT↔ROS 브리지 (서버 명령 수신·결과 회신) |
| `src/potner_mission` | 임무 FSM — 판단은 서버가, 로봇은 수행만 |
| `src/potner_llm` | 대화 LLM (순수 파이썬, rclpy 안 씀) |
| `voice-chat-server/` | 음성 대화 FastAPI (STT→LLM→TTS→스피커/폰) |
| `plant-robot-chat/` | 노트북용 웹 챗 테스트 앱 (Next.js) — `LLM_BACKEND=webchat`의 호출 대상 |
| `tools/` | 수동 도구 — 회전 보정, 시연 조종기, Nav2 액션 목 |
| `docs/WIRING.md` | 장치별 배선·핀·역할 — 하드웨어 주의사항 포함 |
| `docs/CODE_STYLE.md` | 타입힌트는 순수 로직 공개 함수에만 (노드 내부 금지) |
| `docs/MQTT_CONTRACT.md` | 브로커 ACL이 막는 것들 — cross-device 구독 불가 |
| `docs/LLM_CAPABILITIES.md` | LLM이 답할 수 있는 것/없는 것 |
| `docs/LLM_SENSOR_INTEGRATION_PLAN.md` | 센서 실측값 연동 — 서버 대기 중, 전환 절차 포함 |

## 커밋·브랜치

`GIT_CONVENTION.md` 참고. 브랜치는 `Robot-<type>/<작업내용>/<ticket>`,
커밋은 `<type>(<scope>): <제목>`. `src/potner_llm/data/`와 `data/`는
런타임 산출물이라 커밋하지 않는다.
