# 서버 팀 요청 — 로봇이 센서 조회 API를 부를 인증 경로 추가

## ✅ 확정·구현됨 (2026-08-04)

요청대로 구현이 완료됐다 — `Server-feature/device-sensor-auth` 브랜치
(커밋 b675056·6ced0e4·f970c95, 원격 푸시됨). 아래 두 미확정 항목의 답:

1. **토큰: 사진 업로드 토큰 재사용.** `.env`의 `POTNER_UPLOAD_TOKEN` 값을
   그대로 쓴다 (스키마 변경·신규 발급 API 없음).
2. **경로: 제안 그대로** `GET /api/v1/device/sensors/current`,
   헤더 `X-Device-Token`. 오류 계약: 토큰 누락/오류 → 401
   `INVALID_DEVICE_TOKEN`, 활성 배정 없음 → 404 `PLANT_ASSIGNMENT_NOT_FOUND`.

운영 반영 시점: Server-develop 머지 후 **Server-master 머지 + Jenkins 배포**
까지 가야 운영 서버에 열린다(Jenkins 배포 대상은 Server-master뿐). 배포되면
`LLM_SENSOR_INTEGRATION_PLAN.md`의 전환 절차대로 config만 바꾸면 된다.
계약 상세는 서버 트리 `docs/DEVICE-JETSON.md` 11절.

## 배경

로봇(젯슨)에서 음성/텍스트로 대화하는 LLM이 "지금 온도/습도 어때?" 같은
질문에 실제 값으로 답하게 하려 한다. 필요한 값(라즈베리 스테이션이 재는
온습도)은 이미 서버의 `GET /api/v1/plants/{plantId}/sensors/current`에
있다(`SensorQueryController.java`) — TEMPERATURE/HUMIDITY/SOIL_MOISTURE/
ILLUMINANCE 4종을 plant 단위로 합쳐서 반환하므로 어느 장치가 쟀는지는
무관하다. 응답 형태 자체는 문제없이 그대로 쓸 수 있다.

막히는 건 인증뿐이다: 이 엔드포인트는 `@AuthenticationPrincipal
AuthenticatedUser` + `requireOwnedPlant(userId, plantId)`로 보호돼 있어
**사용자 로그인 JWT**가 필요하다. 로봇은 사용자 계정이 아니라 장치이므로
이 경로로는 못 들어간다.

## 요청 사항

이미 같은 문제를 풀어놓은 선례가 있다 — `DevicePhotoController.java`
(`POST /api/v1/device/photos`, 라즈베리 사진 업로드)가 정확히 이 모양이다.
`SecurityConfig.java`에 다음과 같이 적혀 있다:

> "장치는 사용자 JWT를 가질 수 없다. `X-Device-Token`을 서비스가 직접
> 검증하므로 인증이 없는 것이 아니라 인증 수단이 다른 경로다."

그리고 그 컨트롤러는 **plantId를 요청 파라미터로 받지 않는다** — "어느
식물인지는 로봇의 활성 배정에서 서버가 정한다. 장치가 지정할 수 있으면
토큰 하나로 남의 식물에 사진을 넣을 수 있다"는 이유다.

**요청: 센서 조회에도 같은 패턴을 그대로 적용해 주세요.**

- 신규 엔드포인트 `GET /api/v1/device/sensors/current` 추가
  (`/api/v1/device/photos`와 같은 `/device/` 네임스페이스).
- 인증은 `X-Device-Token` — 값 자체는 사진 업로드와 같은 토큰이든 별도
  토큰이든 서버 쪽에서 편한 대로 정해도 된다. 로봇은 어느 쪽이든 config에
  이름만 넣으면 된다.
- `plantId`는 요청에 안 실음 — 사진 업로드와 동일하게 로봇의 활성 배정으로
  서버가 결정.
- 응답 바디는 기존 `CurrentSensorResponse`(`{"plantId", "sensors": [...]}`)
  그대로 재사용하면 된다 — 새로 설계할 것 없이 내부적으로 같은
  `SensorQueryService.getCurrentSensors(...)`를 device-token 인증 경로로
  한 번 더 노출하는 정도의 작업으로 예상한다.
- `SecurityConfig.java`의 permitAll 목록에 `GET /api/v1/device/sensors/current`
  한 줄 추가(사진 업로드의 POST 항목과 같은 자리).

확정해서 알려주시면 좋은 것 두 가지:

1. 토큰 값 — 사진 업로드 토큰(`POTNER_UPLOAD_TOKEN`)을 그대로 쓸지, 센서
   조회 전용 토큰을 새로 발급할지.
2. 엔드포인트 경로/이름이 위 제안과 다르게 확정되면 그 값.

## 로봇 쪽은 이미 준비돼 있음

인증 헤더 이름과 토큰만 정해지면 바로 붙일 수 있도록 로봇 쪽 어댑터
(`SpringSensorSource`, `src/potner_llm/potner_llm/sensor_provider.py`)를
이미 구현해 뒀다 — `token_header`를 `X-Device-Token`으로, `url`을
새 엔드포인트로, `token_env`를 위 1번 답변에 맞는 환경변수 이름으로
바꿔 끼우기만 하면 된다. 자세한 내용은
[`LLM_SENSOR_INTEGRATION_PLAN.md`](LLM_SENSOR_INTEGRATION_PLAN.md).

## 급하지 않음

지금 당장 막힌 기능은 없다 — 로봇 쪽은 이 답변을 기다리지 않고 이미
어댑터·설정 배관까지 마쳤다. 이 엔드포인트가 열리면 config 몇 줄만
바꿔서 실제 값 연동을 마무리한다.
