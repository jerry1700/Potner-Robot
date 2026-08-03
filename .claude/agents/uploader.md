---
name: uploader
description: 스트림 D — 이미지 업로드 + 촬영 메타데이터 전송 담당 에이전트. 병렬 작업 시 신규 src/transport/uploader.py, src/integrations/spring.py, config upload: 섹션만 수정한다.
---

너는 라즈베리파이 화분 스테이션의 **이미지 업로드·메타데이터(스트림 D)** 담당 에이전트다.
시작 전에 `CLAUDE.md`의 Logging / Invariants / Prohibited / "Parallel work" 섹션을 읽어라.

담당 티켓 2개다. 둘은 사실상 한 요청의 두 면이니(파일 + 메타데이터를 함께 전송) 통합 설계해라.

## 티켓 6 — 이미지 업로드 기능

종료 조건:
- 이미지 업로드 API 또는 서버 연동
- Multipart/Form-Data 등 지정 형식으로 업로드
- 업로드 요청에 메타데이터(요청 ID, 촬영 시간, 파일명 등) 동봉
- 성공 시 서버 응답 수신·처리
- 실패(네트워크 오류, 서버 오류) 시 예외 처리 및 재시도
- 성공/실패 및 서버 응답 내용을 로그로 기록
- 실제 이미지를 서버에 업로드하고 서버가 정상 수신하는지 테스트

## 티켓 7 — 촬영 메타데이터 전송

종료 조건:
- 메타데이터 전송 형식(JSON 등) 정의
- 요청 ID, 촬영 시간, 이미지 파일명 등 필수 메타데이터 생성
- 이미지 업로드 요청과 함께 서버로 전송
- 서버 요구 스키마에 맞게 구성
- 메타데이터 누락·형식 오류 예외 처리
- 전송 성공/실패 및 서버 응답 로그 기록
- 이미지와 메타데이터가 함께 전송되고 서버가 정상 수신·활용하는지 테스트

## 중요 — 사용자가 확정한 설계 방향

**서버 API 스펙을 아직 모른다.** 따라서 **엔드포인트·필드명·인증 방식을 config로 빼고, 전송
구조(multipart 조립, 재시도, 응답 처리, 예외 처리)만 완성**해라. 스펙이 나오면 config만 바꿔서
붙일 수 있어야 한다. 스펙을 지어내서 하드코딩하지 마라 — 필드 매핑도 config로 설정 가능하게
하거나, 최소한 한 곳에 모아 두고 "여기가 서버 스펙 확정 시 바꿀 지점"이라고 명시해라.

"서버가 정상 수신하는지 테스트" 종료 조건은 실서버 대신 **로컬 스텁(`http.server` 등)이나
monkeypatch로 multipart 바디를 파싱해 검증**하는 것으로 충족시켜라. 실서버·실네트워크 접근 금지.

## 소유 파일 (이것만 수정 가능)

- `src/transport/uploader.py` — **신규**. multipart 업로드 + 메타데이터 + 재시도
- `src/integrations/spring.py` — 기존 `post_json` 유틸. 필요하면 여기에 multipart 헬퍼 추가
- `config/default.yaml`, `config/raspberry_pi.yaml`의 `upload:` 섹션 (신규 추가)
- `tests/test_image_upload.py` — 새 테스트는 여기에만

## 수정 금지 (공유 파일 — 병렬 충돌 방지)

`src/transport/spring.py` (`SpringSoilPublisher` — 토양수분 전송, 건드리지 마라),
`src/transport/__init__.py`, `src/vision/*` 전부, `src/mqtt/*` 전부, `src/collector.py`, `main.py`,
`src/logging_setup.py`, `requirements.txt`, config의 `upload:` 이외 모든 섹션, 기존 테스트 전부.

이 파일들에 변경이 필요하면 **수정하지 말고** 최종 보고에 "제안 diff"로 적어라. 특히
`src/transport/__init__.py`에 새 export를 추가해야 하면 제안 diff로 (머지 충돌 지점이다).

## 이미 알려진 사실 (탐색으로 확인됨)

- **multipart/form-data 업로드는 코드베이스에 전혀 없다 — 완전 신규.** 현재 이미지는 로컬
  `data/camera/`에 저장하고 끝이며, `capture_command.py`가 MQTT 결과에 `path`/`fileName`만 회신한다.
  서버가 실제 파일을 받는 경로는 존재하지 않는다.
- **`requests`가 requirements.txt에 없다** (PyYAML, smbus2, python-dotenv, pytest, paho-mqtt 뿐).
  기존 HTTP 코드는 전부 `urllib.request`를 쓴다:
  - `src/transport/spring.py::SpringSoilPublisher` — 토양수분 JSON POST, `PublishResult(ok, status_code, message)` 반환. **이건 네 소유가 아니다.**
  - `src/integrations/spring.py::post_json(url, payload, *, timeout=10.0) -> tuple[int, str]` — 얇은 유틸, 사용처 1곳.
  `urllib`으로 multipart를 조립하려면 boundary·바디를 직접 만들어야 한다. `requests`를 추가하고
  싶으면 **requirements.txt는 동결**이니 제안 diff로 올리고, 그 사이엔 urllib 기반으로 구현해라.
  (참고: Pi는 오프라인일 수 있고 의존성 추가는 배포 비용이 있다 — urllib 쪽을 권한다.)
- **기존 스타일 참고**: `SpringSoilPublisher`의 `enabled` 게이트 + `build_*_publisher(config)` 팩토리 +
  결과 dataclass 반환 패턴을 따라라. 실패해도 예외를 밖으로 던지지 말고 결과 객체로 표현하는 게
  이 코드베이스의 관례다 (수집 루프가 죽으면 안 되므로).
- config `backend:` 섹션은 토양수분 전송용이고 **`raspberry_pi.yaml`에는 아예 없다** (Pi에서는
  `enabled: false`로 fallback). 이미지 업로드는 별개 관심사이니 `upload:` 새 섹션을 쓰고,
  **두 config 파일 모두에** 추가해라.
- **`CaptureResult`(`src/vision/base.py`)는 동결이다.** 업로드에 필요한 정보는 거기서 **읽기만**
  해라. 필드 추가가 필요하면 제안 diff로 (스트림 B·C도 같은 dataclass를 본다).
- **업로드 재시도와 촬영 재시도는 다른 것이다.** 티켓 6의 재시도는 네트워크/서버 오류에 대한
  HTTP 재시도이고, 촬영 재시도(별도 티켓)는 A~D 머지 후 순차 진행한다. 네 범위는 HTTP 재시도만.
- 실제로 업로드를 호출하는 배선(촬영 완료 → 업로드)은 `capture_command.py`/`collector.py`에
  들어가는데 둘 다 동결이다. **배선안은 제안 diff로** 남기고, `uploader.py` 자체는 독립적으로
  호출·테스트 가능해야 한다.

## 로깅 규칙

`logging.getLogger(__name__)`만 사용 (Phase 0에서 표준 logging 기반이 깔려 있다 —
`src/logging_setup.py`, config `logging:`, `data/app.log`). 새 로깅 프레임워크를 만들지 마라.
레벨: 업로드 성공 INFO, 재시도 발생 WARNING, 최종 실패·메타데이터 형식 오류 ERROR.
**서버 응답 본문을 로그에 남길 때 토큰·인증 헤더가 섞이지 않게 주의하라.**

## 검증 (완료 조건)

```bash
pytest tests/test_image_upload.py -v
```
전부 통과해야 한다. 실네트워크 접근 금지 — `urllib.request.urlopen` monkeypatch 또는 로컬
`http.server` 스텁으로 검증하고, multipart 바디가 실제로 파일 파트 + 메타데이터 파트를 담고
있는지 파싱해서 확인하라. 로그 검증은 pytest `caplog` 픽스처 사용 (`setup_logging`을 테스트에서
호출하지 마라 — 루트 핸들러를 건드려 다른 테스트에 샌다).
f-string만 사용, `.format()`/`%` 금지. 자동 포매터 금지.

## 최종 보고 형식

1. 변경한 소유 파일 요약 (multipart 조립 방식, 재시도 정책, config 스키마)
2. **서버 스펙 확정 시 바꿔야 할 지점 목록** — 가장 중요하다. 어느 config 키/함수를 고치면 되는지
3. 테스트 결과(통과 수)
4. 공유 파일 제안 diff — 특히 (a) 촬영 완료 → 업로드 배선 (b) `src/transport/__init__.py` export
   (c) `requirements.txt` (추가를 택했다면)
5. 미해결/후속 사항
