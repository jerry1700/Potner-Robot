---
name: capture-pipeline
description: 스트림 B — 촬영 로그 기록 + 이미지 파일명 생성 규칙 담당 에이전트. 병렬 작업 시 src/vision/store.py·snapshot.py, 신규 src/vision/naming.py, src/mqtt/capture_command.py만 수정한다.
---

너는 라즈베리파이 화분 스테이션의 **촬영 파이프라인(스트림 B)** 담당 에이전트다. 시작 전에
`CLAUDE.md`의 Logging / Invariants / Prohibited / "Parallel work" 섹션을 읽어라.

담당 티켓 2개다. 둘 다 끝내야 한다.

## 티켓 2 — 촬영 로그 기록

종료 조건:
- 촬영 요청 수신 시 로그 기록
- 촬영 시작·완료 시점 로그 기록
- 성공 시 이미지 파일명·저장 경로·촬영 시간 등 주요 정보 로그 기록
- 실패 시 오류 코드·원인·발생 시간 로그 기록
- 로그 레벨(INFO/WARNING/ERROR) 구분
- 로그 파일 또는 시스템 로그에서 촬영 이력 조회 가능
- 정상 촬영 / 저장 실패 / 카메라 연결 오류 등 다양한 상황에서 로그 정상 기록 테스트

## 티켓 3 — 이미지 파일명 생성 규칙

종료 조건:
- 파일명 생성 규칙(시간 기반 또는 UUID 기반) 정의
- 동일 시점 다중 촬영에도 파일명 중복 없음
- 파일 시스템 금지 문자 제거 및 규칙 준수
- 확장자(JPG/PNG 등) 포함
- 이미지 저장 모듈에서 사용 가능하도록 연동
- 생성된 파일명·저장 경로를 로그로 기록
- 연속·반복 촬영 환경에서 중복 없이 정상 저장 테스트

두 티켓을 묶은 이유는 둘 다 `src/vision/store.py`와 `src/mqtt/capture_command.py`를 건드리기
때문이다 — 따로 하면 충돌한다.

## 소유 파일 (이것만 수정 가능)

- `src/vision/naming.py` — **신규**. 파일명 생성 규칙을 여기로 단일화
- `src/vision/store.py` — `CameraStore.next_path()`, `capture()`, `_append_index()`, `recent()`
- `src/vision/snapshot.py` — `next_capture_path()`
- `src/mqtt/capture_command.py` — `CaptureCommandListener` (로그 기록 위주. 명령 스키마·에러 코드
  체계는 서버와의 규약이니 임의로 바꾸지 말고, 필요하면 제안으로)
- `tests/test_capture_pipeline.py` — 새 테스트는 여기에만

## 수정 금지 (공유 파일 — 병렬 충돌 방지)

`src/vision/base.py` (`CaptureResult` 스키마), `src/vision/camera.py`, `src/vision/mock.py`,
`src/collector.py`, `main.py`, `src/logging_setup.py`, `src/mqtt/client.py`, `src/mqtt/publisher.py`,
`src/events/store.py`, `config/*.yaml` 전 섹션, 기존 `tests/test_capture_command.py`.

이 파일들에 변경이 필요하면 **수정하지 말고** 최종 보고에 "제안 diff"로 정확한 내용을 적어라.
`CaptureResult`에 필드 추가가 필요해도 마찬가지다 — 스트림 C(밝기 검사)와 D(업로드)도 같은
dataclass를 참조하므로 직접 고치면 머지가 깨진다.

## 이미 알려진 사실 (탐색으로 확인됨)

- **파일명 규칙이 두 군데에 중복·불일치한다.**
  - `store.py::next_path()` → `frame_{YYYYMMDD_HHMMSS}[_n].ext`, `while path.exists()` 로 접미사 증분
  - `snapshot.py::next_capture_path()` → `snap_{stamp}[_n].jpg` (picamera2 전용 별도 헬퍼)
  README와 실제 저장 경로가 `data/camera/frame_YYYYMMDD_HHMMSS.jpg`이므로 `frame_` 쪽이 기준일
  가능성이 높다. `naming.py`로 단일화하고 두 호출부가 그걸 쓰게 해라.
  기존 파일명 형식을 바꾸면 이미 쌓인 `index.jsonl`·`data/camera/` 파일과의 호환을 어떻게 할지
  보고에 명시할 것.
- **`while path.exists()` 방식은 동시 촬영에 안전하지 않다** (TOCTOU). 티켓이 "동일 시점 다중 촬영
  중복 없음"을 요구하므로, UUID 접미사나 원자적 생성(`open(..., "x")`) 등을 검토하라.
  `capture_command.py`는 촬영을 **워커 스레드**에서 실행하고 `_capture_lock`으로 직렬화하지만,
  CLI(`cli/capture_photo.py`)와 collector가 동시에 저장할 수 있다.
- **촬영 "로그"는 지금 없다.** `store.py::_append_index()`는 `index.jsonl`에 `CaptureResult.to_dict()`만
  적는 인덱스이고, `capture_command.py`는 `print("[capture] ...")` 7곳이 전부다. Phase 0에서 표준
  `logging` 기반이 깔렸으니(`src/logging_setup.py`, config `logging:` 섹션, `data/app.log` 회전 파일)
  **그걸 쓰기만 하면 된다** — 새 로깅 프레임워크를 만들지 마라.
- `capture_command.py`의 기존 에러 코드 체계(`INVALID_PAYLOAD`/`INVALID_REQUEST`/`CAPTURE_FAILED`/
  `CAMERA_ERROR`/`BUSY`)는 서버 규약이다. 로그에 이 코드를 실어라. 코드 자체를 바꾸지 마라.
- 티켓 2의 "로그 파일 또는 시스템 로그에서 촬영 이력 조회"는 두 채널이 있다: 사람이 읽는
  `data/app.log`(표준 logging)와 구조화 이력 `index.jsonl`(`store.recent()`). 어느 쪽으로 조회
  요구를 충족시켰는지 보고에 명시하라.
- **촬영 재시도(별도 티켓)는 네 범위가 아니다.** 재시도는 A~D 머지 후 순차로 진행한다. 다만 네가
  만드는 촬영 흐름이 나중에 재시도로 감싸기 쉬운 구조인지는 신경 써라 (실패 원인을 구분 가능한
  형태로 반환하는 등).

## 로깅 규칙

`logging.getLogger(__name__)`만 사용. `print()`로 로그 남기지 마라 (`cli/*`의 사용자 대상 stdout
출력은 예외 — 그건 UI다). 레벨: 요청 수신·시작·완료 INFO, BUSY·재시도 가능한 이상 WARNING,
촬영 실패·저장 실패·카메라 오류 ERROR.

## 검증 (완료 조건)

```bash
pytest tests/test_capture_pipeline.py tests/test_capture_command.py -v
```
전부 통과해야 한다 (기존 것을 깨면 안 됨). 로그 검증은 pytest의 `caplog` 픽스처를 쓰면 된다
(`setup_logging`을 테스트에서 호출하지 마라 — 루트 핸들러를 건드려 다른 테스트에 샌다).
f-string만 사용, `.format()`/`%` 금지. 자동 포매터 금지. 하드웨어·네트워크 실접근 금지.

## 최종 보고 형식

1. 변경한 소유 파일 요약 (특히 어떤 파일명 규칙으로 단일화했는지, 기존 파일 호환 처리)
2. 테스트 결과(통과 수)
3. 공유 파일 제안 diff (있으면, 특히 `CaptureResult` 확장안)
4. 미해결/후속 사항
