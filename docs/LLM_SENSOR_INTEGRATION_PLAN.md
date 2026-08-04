# LLM 대화에 라즈베리 온습도 실측값 연동 — 구현 계획

## 진행 상태 (2026-08-04) — 실데이터 E2E 검증 완료

**서버 쪽도 구현됐다** — `Server-feature/device-sensor-auth` 브랜치(커밋
b675056·6ced0e4·f970c95, 푸시됨)에 `GET /api/v1/device/sensors/current`가
사진 업로드와 같은 uploadToken(X-Device-Token) 재사용으로 구현·테스트돼
있다. 운영 배포는 Server-develop → Server-master 머지 후 Jenkins가 한다
(Jenkinsfile의 배포 대상은 Server-master뿐).

**머지 전 실데이터 E2E 검증 통과 (2026-08-04, 이 브랜치)** — 새 엔드포인트가
운영에 깔리기 전이므로, 배관이 완전히 같은 사용자 JWT 변형(전환 절차의
"사용자 JWT 방식")으로 운영 서버 실측값을 통과시켰다:

- 기준값: 운영 서버 `GET /plants/4244c331-…(Vice)/sensors/current` →
  온도 26.6°C / 습도 48.0% / 토양 10.0% (STALE — 라즈베리 마지막 전송
  08-03 08:54Z. STALE 값도 그대로 쓰는 설계 확인), 조도 NO_DATA.
- provider 단독: `SpringSensorSource` snapshot이 위 값과 정확히 일치,
  NO_DATA→None(미측정), 토양 10%→'건조' 판정, 로그 `센서 조회 성공 (spring:…)`.
- 실대화(GMS gpt-4.1-nano, CLI): "지금 온도 어때?"→**"온도는 26.6도예요.
  적정한 상태라고 생각돼요."**, "습도는 어때?"→**"습도는 48%로 적당한
  편이에요."**, "물 줘야 해?"→**"네, 토양이 건조해서 물이 필요해요."**
  — 매 턴 factcheck 통과.
- 목데이터 배제 증명: 검증 동안 `sensors.json` 더미를 99.9°C/1% 함정값으로
  바꿔 뒀다 — 답변 어디에도 안 나옴 = 파일 폴백을 타지 않음. 잘못된
  plantId로는 404를 provider가 흡수하고 None(미측정) 폴백하는 것도 확인.

남은 것: 서버 운영 배포 후 [전환 절차](#전환-절차-서버-답변-후) 그대로
X-Device-Token 최종형으로 config 전환(토큰은 `.env`의 `POTNER_UPLOAD_TOKEN`
재사용 확정). CLI는 `python -m potner_llm.cli`로 실행한다(파일 직접 실행은
상대 임포트 오류).

## 진행 상태 (2026-08-03)

**아래 1~4번(어댑터·설정·배관·테스트)은 구현·검증 완료.** 남은 것은 서버에
장치용 센서 조회 경로가 열리면 config 몇 줄을 바꾸는 것뿐이다
(→ [전환 절차](#전환-절차-서버-답변-후)).

실측으로 확인한 것:

- **e2e 통과** — `sensors.json`에 온습도를 넣고 실제 GMS LLM 을 돌린 결과,
  판정(26.1°C→적정, 55%→적정) → `get_sensor_data` 툴 결과 → 최종 답변까지
  값이 그대로 흘렀다. 실제 답변: *"온도는 26.1도 정도로 적당해요"*,
  *"습도는 55퍼센트 정도예요"*. **소스만 갈아끼우면 되는 상태다.**
- **배관이 서버까지 닿는다** — `http://i15e104.p.ssafy.io/api/v1` 로 실제
  요청해 확인:
  - `GET /health` → 200 `{"status":"UP"}`
  - `GET /plants/{id}/sensors/current` 인증 없음 → 401 `ACCESS_TOKEN_REQUIRED`
  - 같은 경로에 `X-Device-Token` → 401 `ACCESS_TOKEN_REQUIRED`
    (헤더가 **무시된다** — 이 경로엔 장치 토큰 처리가 없다는 증거)
  - 같은 경로에 장치 토큰을 `Bearer` 로 → 401 **`INVALID_ACCESS_TOKEN`**
    (에러 코드가 달라진다 = JWT 필터가 헤더를 **읽었고** 토큰만 무효라는 뜻.
    URL·헤더 조립이 맞다는 확인이다)
- **서버 쪽은 아직 미구현** — `origin/Server-develop` 최신(`1c4feba`)에도
  장치용 센서 컨트롤러가 없다(`SensorQueryController`=사용자 JWT,
  `DevicePhotoController`=장치 토큰, 둘뿐). `/v3/api-docs` 는 500 이라
  라우트 열거는 불가해서 소스 트리로 확인했다.
  요청 내용은 [`SERVER_REQUEST_SENSOR_AUTH.md`](SERVER_REQUEST_SENSOR_AUTH.md).

주의: `/device/sensors/current` 에 대한 401 은 "라우트 없음"이 아니라
Security 필터가 라우팅보다 먼저 돌아서 나오는 것이다. 즉 **401 만으로는
구현 여부를 판별할 수 없다** — 열렸는지 확인하려면 유효한 토큰으로 찔러
404/200 을 봐야 한다.

## 배경

음성/텍스트 대화에서 "지금 온도 어때?" 같은 질문에 LLM이 실제 값으로 답하지
못하고 "미측정"만 돌려준다. 원인은 판정 로직이 없어서가 아니라, 실제 값을
채워 넣는 배관이 아예 없기 때문이다.

- `src/potner_llm/potner_llm/models.py`의 `SensorSnapshot`, `status.py`의
  `PlantStatus`에 이미 `temperature`/`humidity` 필드가 있고, `tools.py`의
  `get_sensor_data` 툴도 온도·습도 라벨(저온/고온/적정)까지 다 만들어서
  LLM에 넘길 준비가 돼 있다. **판정 파이프라인은 완성돼 있다.**
- 그런데 `voice-chat-server/app.py:102-104`와 `cli.py:53-54`가 둘 다
  `src/potner_llm/data/sensors.json`을 읽는 `FileSensorSource`를
  하드코딩하고 있고, **이 파일은 디스크에 없고 아무도 쓰지 않는다.**
  로봇(젯슨) 자체엔 온습도 센서가 없으므로(DHT11은 라즈베리 스테이션에만
  있음, `docs/MQTT_CONTRACT.md:37-38`) 당연한 결과다.
- 라즈베리 온습도는 이미 서버의
  `GET /api/v1/plants/{plantId}/sensors/current`에 있다 — 어느 장치가
  쟀는지와 무관하게 plant 단위로 4종 센서(TEMPERATURE/HUMIDITY/
  SOIL_MOISTURE/ILLUMINANCE)를 합쳐서 반환한다
  (`SensorQueryController.java`, `SensorQueryService.getCurrentSensors`).
- 이 엔드포인트는 `@AuthenticationPrincipal AuthenticatedUser` +
  `requireOwnedPlant(userId, plantId)`로 보호돼 있다 — **사용자 로그인
  JWT가 필요**하고, 그 사용자가 해당 plant를 소유해야 한다. 라즈베리
  사진 업로드에 쓰는 `X-Device-Token` 같은 장치 토큰이 아니다. 이 부분은
  로봇 팀 단독으로 결정할 수 없어 [`SERVER_REQUEST_SENSOR_AUTH.md`](SERVER_REQUEST_SENSOR_AUTH.md)로
  별도 요청한다.

## 응답 모양이 그대로 안 맞는다

서버 응답은 평평한 `{"temp": 26.1, "humidity": 55.0}`가 아니라 배열이다:

```json
{
  "plantId": "...",
  "sensors": [
    {"sensorType": "TEMPERATURE", "unit": "CELSIUS", "value": 23.5,
     "measuredAt": "...", "status": "NORMAL", "thresholdMin": 15.0, "thresholdMax": 30.0},
    {"sensorType": "HUMIDITY", "unit": "PERCENT", "value": 55.0, ...},
    {"sensorType": "SOIL_MOISTURE", ...},
    {"sensorType": "ILLUMINANCE", ...}
  ]
}
```

`status`는 `LOW|NORMAL|HIGH|NOT_APPLICABLE|NO_DATA|STALE` — 서버 자체의
생육 기준 판정이다(`SensorStatus.java`). `NO_DATA`가 아니면 `value`가
있다고 봐도 된다. `STALE`(오래된 값)도 값 자체는 있으므로 그대로 쓴다 —
로봇 쪽 `classify_metric`이 절대 임계값으로 다시 판정하므로 서버 판정과
중복되어도 문제없다.

기존 `HttpSensorSource`(`sensor_provider.py:210-224`)는 응답이 이미
평평한 dict라고 가정하므로 이 모양을 그대로 못 먹는다. 재구성 단계가
하나 필요하다.

## 설계

### 1. 새 소스: `SpringSensorSource` (`src/potner_llm/potner_llm/sensor_provider.py`)

`HttpSensorSource` 옆에 추가한다. 기존 클래스를 고치지 않고 나란히 둔다 —
`HttpSensorSource`는 이미 평평한 JSON을 주는 다른 소스(데모/테스트용)에
계속 쓰일 수 있으므로 손대지 않는다.

```python
_SENSOR_TYPE_TO_FIELD = {
    "TEMPERATURE": "temperature",
    "HUMIDITY": "humidity",
    "SOIL_MOISTURE": "soil",
    "ILLUMINANCE": "light",
}

class SpringSensorSource:
    """Spring 서버의 GET /plants/{plantId}/sensors/current 를 읽는 소스.

    응답이 센서별 배열이라 parse_snapshot이 기대하는 평평한 dict로
    재구성한다. NO_DATA는 값을 안 실어(=None), 나머지는 value를 그대로
    쓴다 — STALE이어도 마지막 실측값은 유효하다.
    """

    def __init__(self, base_url: str, plant_id: str, *,
                 token_provider: Callable[[], str], timeout_seconds: float = 5.0):
        ...

    def read(self) -> Optional[SensorSnapshot]:
        url = f"{self.base_url.rstrip('/')}/api/v1/plants/{self.plant_id}/sensors/current"
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {self.token_provider()}",
            },
        )
        with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
            body = json.loads(response.read().decode("utf-8"))

        flat = {}
        for item in body.get("sensors", []):
            field = _SENSOR_TYPE_TO_FIELD.get(item.get("sensorType"))
            if field is None or item.get("status") == "NO_DATA":
                continue
            flat[field] = item.get("value")
        return parse_snapshot(flat)

    def describe(self) -> str:
        return f"spring:{self.plant_id}"
```

`token_provider`는 콜백이다 — 토큰 발급/갱신 방식이 아직 서버팀 답변
대기 중이라([`SERVER_REQUEST_SENSOR_AUTH.md`](SERVER_REQUEST_SENSOR_AUTH.md)),
지금 확정할 수 있는 건 "매 호출마다 최신 토큰 문자열을 돌려주는 함수"라는
인터페이스뿐이다. 구현체(고정 토큰 env 변수 / refresh 플로우 자동화)는
서버팀 답변에 따라 나중에 채운다.

`urllib`만 쓴다 — `SpringSensorSource`가 있는 `sensor_provider.py`는
docstring(1-3행)에 rclpy를 안 쓴다는 원칙이 있고, 기존 `HttpSensorSource`도
`requests`가 아니라 stdlib `urllib`을 쓰고 있어 그 관례를 따른다.
(`voice-chat-server/speech.py`가 GMS 호출에 `requests`를 강제하는 것과는
다른 얘기 — 그건 GMS 프록시가 urllib 응답 바디를 유실하는 별도 이슈였고,
Spring 서버엔 해당하지 않는다.)

### 2. `create_sensor_provider()` 확장 (`sensor_provider.py:270-300`)

`kind == "spring"` 분기를 추가한다:

```yaml
sensor:
  source: spring
  base_url: http://i15e104.p.ssafy.io/api/v1     # 또는 SPRING_BASE_URL env
  plant_id: "..."                                 # 또는 PLANT_ID env
  timeout_seconds: 5
  # 토큰 조달 방식은 서버팀 답변에 따라 확정 — token_env 또는 별도 refresh 설정
```

기존 `file`/`http`/`none` 분기는 그대로 둔다 — 데모·테스트에서 여전히
쓸모 있다.

### 3. `app.py` / `cli.py` 하드코딩 제거

```python
# voice-chat-server/app.py:99-104, cli.py:51-54 — 둘 다 이 패턴으로 교체
sensor_provider = create_sensor_provider(self.config) or SensorDataProvider(
    FileSensorSource(LLM_PACKAGE_DIR / "data" / "sensors.json")
)
```

`create_sensor_provider`가 `None`을 돌려주면(=`sensor:` 섹션이 없거나
`source: none`) 기존 파일 기반 데모 동작으로 폴백한다 — 설정을 안 건드린
개발 환경은 지금과 똑같이 동작해야 한다.

### 4. `llm.yaml`에 `sensor:` 섹션 추가

지금 `src/potner_llm/config/llm.yaml`에는 `llm:`/`conversation:`만 있고
`sensor:` 섹션 자체가 없다. 위 설계대로 새로 추가한다.

## 건드릴 파일

- `src/potner_llm/potner_llm/sensor_provider.py` — `SpringSensorSource` 추가,
  `create_sensor_provider`에 `spring` 분기 추가
- `src/potner_llm/config/llm.yaml` — `sensor:` 섹션 신설
- `voice-chat-server/app.py` — 하드코딩된 `FileSensorSource` 생성부 교체
- `src/potner_llm/potner_llm/cli.py` — 동일 교체
- `tests/test_llm_sensors.py` — `SpringSensorSource` 테스트 추가 (아래)

## 테스트 계획

전부 네트워크·GMS 없이 순수 로직으로 검증 가능 (`llm-test` 스킬 대상):

- 정상 응답(4종 전부 값 있음) → `SensorSnapshot`에 4개 필드 모두 채워짐
- `status: "NO_DATA"`인 항목은 `None`으로 빠짐 (예: 조도 센서 없는 스테이션)
- `status: "STALE"`인 항목은 그래도 값이 채워짐(마지막 실측값 재사용 확인)
- `sensorType`이 매핑 표에 없는 값(서버가 나중에 센서 종류를 늘렸을 때)은
  무시하고 나머지는 정상 처리 — `parse_snapshot`이 이미 이런 방어를
  갖고 있으므로 그대로 재사용되는지 확인
- 인증 실패(401)·타임아웃 → `SensorDataProvider.snapshot()`이 예외를
  삼키고 마지막 성공값 또는 `None`으로 폴백하는 기존 동작 그대로 확인
  (`sensor_provider.py:241-259`, 이미 있는 방어라 회귀만 확인하면 됨)
- `create_sensor_provider({"sensor": {"source": "spring", ...}})` →
  `SpringSensorSource` 인스턴스 생성 확인, 필수 키 누락 시 `ValueError`

## 전환 절차 (서버 답변 후)

서버에 장치용 센서 경로가 열리면 **코드 수정 없이** 아래만 바꾼다.

1. `.env` 에 토큰 추가 (값은 서버팀이 알려주는 것. 사진 업로드 토큰을
   그대로 쓰기로 하면 `POTNER_UPLOAD_TOKEN` 을 그대로 가리키면 된다):

   ```
   POTNER_SENSOR_TOKEN=...
   ```

2. `src/potner_llm/config/llm.yaml` 의 `sensor:` 섹션을 켠다:

   ```yaml
   sensor:
     source: spring
     url: http://i15e104.p.ssafy.io/api/v1/device/sensors/current
     token_header: X-Device-Token      # 서버가 Authorization 방식으로 확정하면 그 값으로
     token_env: POTNER_SENSOR_TOKEN
     timeout_seconds: 5
   ```

   `token_header: Authorization` 으로 두면 `SpringSensorSource` 가
   `Bearer ` 접두를 자동으로 붙인다. 그 외 헤더 이름은 토큰을 날값으로
   싣는다(라즈베리 사진 업로드의 `X-Device-Token` 관례와 동일).

   경로가 사용자 JWT 방식(`/plants/{plantId}/sensors/current`)으로
   확정되면 `url` 에 실제 plantId 를 박고 `token_header: Authorization` 로
   두면 된다 — 이 경우 plantId 가 설정에 노출되는 것을 감수해야 한다.

3. 확인:

   ```bash
   # 서버에서 값이 실제로 오는지
   python -c "
   import sys; sys.path.insert(0, 'src/potner_llm')
   import potner_llm.cli as cli
   cli._load_env_file(__import__('pathlib').Path('.env'))
   cfg = cli._load_config(cli.PACKAGE_DIR / 'config' / 'llm.yaml')
   p = cli._build_sensor_provider(cfg)
   print(p._describe(), p.snapshot())
   "

   # 대화로 확인
   python src/potner_llm/potner_llm/cli.py
   #   you> 지금 온도 어때?
   ```

   `snapshot()` 이 `None` 이면 로그에 실패 이유가 남는다(`SensorDataProvider`
   가 예외를 흡수하고 WARNING 으로 남긴다). 401 이면 토큰/헤더,
   404 면 URL 을 다시 본다.

4. 켠 뒤에는 `src/potner_llm/data/sensors.json` 이 더 이상 쓰이지 않는다
   (`create_sensor_provider` 가 provider 를 돌려주므로 파일 폴백을 타지
   않음). 그 파일은 gitignore 대상인 개발용 더미이므로 지우든 두든 무해하다.

## 왜 이 순서였는지

인증 방식이 서버팀 답변 대기 중이었지만, `token_provider` 를 콜백으로
두면 그 아래(응답 재구성·판정·툴·프롬프트)는 인증과 무관하다. 그래서
답변을 기다리지 않고 배관을 다 만들고 e2e 까지 증명해 뒀다. 남은 것이
config 몇 줄로 줄어들어서, 서버 작업이 끝나는 즉시 붙일 수 있다.
