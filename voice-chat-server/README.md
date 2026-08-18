# 오린카 음성 대화 서버

휴대폰 브라우저(같은 Wi-Fi)로 로봇에 접속해 **음성으로 대화**하는 로컬 웹서버.

```
🎤 녹음 (MediaRecorder) → POST /api/voice-chat-stream
   → STT (GMS whisper-1)
   → LLM (potner_llm DialogueService — tool use·페르소나·히스토리 그대로)
   → TTS (GMS gpt-4o-mini-tts)
   → 폰 브라우저 재생  또는  로봇 스피커 재생  (AUDIO_SINK로 선택)
```

별도 클라우드 계정 없이 **기존 `GMS_API_KEY` 하나**로 STT/LLM/TTS가 모두 동작한다.
(실측: STT ~1.5초, LLM ~2~4초, TTS ~2.5초 → 턴당 약 6~8초)

## 실행 (로봇: Jetson / RPi)

```bash
# 1) 의존성 (최초 1회)
pip install -r voice-chat-server/requirements.txt

# 2) 레포 루트의 .env에 GMS_API_KEY가 있는지 확인

# 3) 서버 시작 (레포 루트에서)
python -m uvicorn app:app --app-dir voice-chat-server --host 0.0.0.0 --port 8080
```

`--host 0.0.0.0`이어야 휴대폰에서 접속할 수 있다 (127.0.0.1이면 로봇 안에서만 보임).

### 로봇 스피커로 소리를 내려면

```bash
# 1) speaker_node가 떠 있어야 한다 (robot.launch.py에 포함)
source install/setup.bash
ros2 launch potner_bringup robot.launch.py

# 2) 같은 셸 환경에서 음성 서버 (rclpy가 필요해 source가 선행돼야 한다)
source install/setup.bash
AUDIO_SINK=speaker STT_BACKEND=gms LLM_BACKEND=potner \
  python -m uvicorn app:app --app-dir voice-chat-server --host 0.0.0.0 --port 8080
```

- `STT_BACKEND=gms` — 젯슨에서 faster-whisper(large-v3-turbo)를 돌리면 메모리와
  GPU를 주행·인식과 다투게 된다. 로컬 STT는 개발 PC 전용으로 둔다.
- `LLM_BACKEND=potner` — `webchat`은 노트북의 Next.js(`plant-robot-chat`)가 떠
  있어야 한다. 로봇 단독으로 닫으려면 `potner`.
- `source install/setup.bash`를 빼먹으면 rclpy가 없어 **브라우저 재생으로 조용히
  물러난다**(로그에 경고). 대화 자체는 계속 되므로 소리가 폰에서 나면 이걸 의심.

## 휴대폰에서 접속

1. **로봇의 로컬 IP 확인** (로봇 터미널에서):
   ```bash
   hostname -I        # Linux(Jetson/RPi) — 첫 번째 주소가 보통 Wi-Fi IP (예: 192.168.0.42)
   ```
   Windows에서 개발 테스트 중이면: `ipconfig` → "무선 LAN 어댑터"의 IPv4 주소.

2. **방화벽 포트 개방** (필요한 경우만):
   ```bash
   # Ubuntu(Jetson/RPi) — ufw를 쓰는 경우
   sudo ufw allow 8080/tcp
   ```
   Windows 개발 PC라면: 처음 uvicorn 실행 시 뜨는 방화벽 허용 팝업에서 "허용",
   또는 관리자 PowerShell에서
   `netsh advfirewall firewall add rule name="orinca-voice" dir=in action=allow protocol=TCP localport=8080`

3. 휴대폰이 **로봇과 같은 Wi-Fi**에 붙어 있는지 확인 후, 브라우저에서
   `http://<로봇IP>:8080` 접속.

## ⚠️ 마이크가 안 열릴 때 (가장 흔한 문제)

브라우저는 **보안 컨텍스트(HTTPS 또는 localhost)에서만** 마이크(`getUserMedia`)를
허용한다. `http://192.168.x.x:8080`은 여기 해당하지 않아 마이크 버튼이 막힌다.

**방법 A — Android Chrome 플래그 (간단, 데모용 추천):**
1. 휴대폰 Chrome 주소창에 `chrome://flags/#unsafely-treat-insecure-origin-as-secure`
2. 입력란에 `http://<로봇IP>:8080` 추가 → Enabled → 브라우저 재시작

**방법 B — 자체 서명 HTTPS (iPhone은 이 방법만 가능):**
```bash
# 로봇에서 인증서 생성 (최초 1회)
openssl req -x509 -newkey rsa:2048 -nodes -days 365 \
  -keyout key.pem -out cert.pem -subj "/CN=orinca.local"

python -m uvicorn app:app --app-dir voice-chat-server --host 0.0.0.0 --port 8443 \
  --ssl-keyfile key.pem --ssl-certfile cert.pem
```
휴대폰에서 `https://<로봇IP>:8443` 접속 → 경고 화면에서 "고급 → 계속 진행".

## API

| 엔드포인트 | 설명 |
|---|---|
| `GET /` | 녹음/재생 테스트 페이지 |
| `GET /monitor` | 대화 실시간 모니터 + 타이핑 입력 (노트북 브라우저에서 열기) |
| `GET /api/events` | 대화 턴 SSE 스트림 (`/monitor`가 구독) |
| `POST /api/text-chat` | JSON `{text, session_id}` → 타이핑으로 같은 대화에 참여 |
| `GET /api/health` | 서버·LLM 상태 + 오디오 싱크(`audio_sink`/`audio_format`/`plays_on_browser`) |
| `POST /api/voice-chat-stream` | `audio`+`session_id` → SSE (`user_text`→`text_delta`*→`audio_chunk`*→`done`). **웹페이지가 쓰는 기본 경로** |
| `POST /api/voice-chat` | `audio`(파일) + `session_id`(폼) → `{user_text, reply_text, audio_b64}` (통짜 JSON, 폴백용) |
| `POST /api/session/end` | 대화 히스토리 저장 + 맥락 초기화 + 재생 중단 (페이지 이탈 시 자동 호출) |

세션은 `session_id`별로 대화 맥락이 유지되고, 종료 시 `src/potner_llm/data/`의
conversation 백엔드에 저장돼 다음 접속에서 이어진다.

## LLM 백엔드 선택

`LLM_BACKEND` 환경변수로 두뇌를 고른다:

- **`potner` (기본)** — potner_llm DialogueService (오린카, GMS claude-opus-4-8).
  대화 컨텍스트 조립(context_builder) + 센서 조회 툴 + 사실성 검증(factcheck,
  센서와 상충하는 답은 재생성 후 상태 기반 폴백)이 적용된다.
- **`webchat`** — plant-robot-chat의 `/api/chat`을 HTTP로 호출.
  초록이 페르소나 + 안전필터 + 툴 6개 + 장기기억 요약이 그대로 적용된다.
  **웹 챗 dev 서버가 함께 떠 있어야 한다:**
  ```bash
  # 별도 터미널에서 (plant-robot-chat/.env.local 에 GMS_API_KEY 필요)
  cd plant-robot-chat && npm run dev
  ```
  웹 챗이 3000이 아닌 포트에 떴으면 `WEBCHAT_URL=http://127.0.0.1:<포트>`로 지정.
  히스토리는 음성 서버(`webchat_llm.py`)가 세션별로 관리하며, 20턴 초과분은
  `/api/summarize`로 압축해 장기기억으로 넘긴다.
웹 챗 백엔드를 선택했는데 웹 챗 서버가 꺼져 있으면 대화가 죽지 않고 안내
문구로 폴백한다.

## 대화 모니터 (노트북에서 보기 + 타이핑 참여)

서버를 띄운 노트북에서 `http://localhost:8080/monitor`를 열면 휴대폰에서 오간
대화가 실시간으로 표시된다 (SSE — 모니터 접속 **이후**의 턴부터 보임).
같은 Wi-Fi의 다른 기기에서도 `http://<노트북IP>:8080/monitor`로 볼 수 있다.

하단 입력창으로 타이핑하면 **휴대폰 음성과 같은 세션**(`voice-web`)에 이어져,
폰으로 말한 내용을 노트북에서 타이핑으로 이어받을 수 있다 (반대도 됨).

## 실제 센서 연결

센서값은 `src/potner_llm/data/sensors.json`에서 읽는다
(`sensor_provider.FileSensorSource`). 이 파일의 숫자를 바꾸면 다음 질문부터
바로 반영된다. 로봇에 올릴 때는 MQTT/ROS 노드가 이 파일을 갱신하거나
`CallbackSensorSource`로 교체하면 LLM tool use(`get_plant_status`)가 실제
센서값으로 대답한다. 파일이 없거나 깨져도 서버는 죽지 않는다(미측정 라벨 폴백).

## STT 백엔드 (로컬 faster-whisper / GMS)

`STT_BACKEND` 환경변수 — **`local`(기본)** | `gms`.

- `local`: faster-whisper를 GPU(cuda/float16)로 돌린다. 짧은 발화 기준 0.3~0.6초로
  GMS whisper-1(1.3~2.1초)보다 1~1.5초 빠르다. GPU가 안 잡히면 cpu/int8,
  그것도 안 되면 **GMS로 자동 폴백**하므로 설치 실패가 서비스 실패가 되진 않는다.
  서버 시작 직후 모델 로드가 끝나기 전에도 GMS 폴백으로 바로 대화 가능.
- 모델은 `WHISPER_MODEL`로 변경 (기본 `large-v3-turbo`; 가벼운 노트북은 `small`).
  **최초 실행 시 HuggingFace에서 ~1.6GB 다운로드** (1회, 이후 캐시).
- TTS는 오린카 말투(instructions 톤) 유지를 위해 GMS `gpt-4o-mini-tts` 그대로 쓴다.

## 소리가 나는 곳 (AUDIO_SINK)

`AUDIO_SINK` 환경변수 — **`browser`(기본)** | `speaker` | `both`.

| 값 | 소리가 나는 곳 | TTS 포맷 | 쓰는 자리 |
|---|---|---|---|
| `browser` | 폰 브라우저 | mp3 | 개발 PC. wav의 1/3 크기라 Wi-Fi로 가볍다 |
| `speaker` | 로봇 스피커 | wav | 실제 데모 |
| `both` | 둘 다 | wav | 모니터로 보면서 로봇으로 들을 때 |

`speaker`/`both`에서는 SSE의 `audio_chunk`를 폰으로 보내지 않는다 — 두 곳에서
같은 소리가 겹쳐 울리면 메아리가 된다.

### 로봇 스피커로 가는 길

로봇 스피커는 `speaker_node`가 단독으로 소유한다. 음성 서버가 `aplay`를 직접
띄우면 `mission_manager`의 귀가 인사말과 오디오 디바이스를 다투게 된다. 그래서
조각을 파일로 떨어뜨리고 **경로만** ROS 토픽으로 넘긴다.

```
voice-chat-server                     speaker_node (potner_base)
  조각을 스풀에 쓰고 ──> tts/play_audio ──> PlaybackQueue
  {"turnId","seq","path","final"}          순서·중복·선점 판단
  세션 종료 시     ──> tts/cancel    ──> 남은 조각 폐기 + 재생 중단
```

- 스풀 경로는 `TTS_SPOOL_DIR`(기본 `/tmp/potner-tts`). `potner_params.yaml`의
  `speaker.audio_spool_dir`과 **같은 곳**을 가리켜야 한다. 노드는 봉투로 들어온
  경로가 이 아래인지 확인하고 벗어나면 거부한다.
- 조각은 최근 40개만 남기고 자동으로 지운다.
- **순서·중복·선점 제어는 노드 쪽**(`potner_base/audio_queue.py`)에 있다.
  같은 `(turnId, seq)` 재전송은 무시하고, 새 턴이 오면 이전 답변의 대기 조각을
  버리고 재생 중인 조각까지 끊는다(barge-in). 인사말은 선점하지 않는다.

### TTS 포맷을 mp3로 되돌리려면

`speaker` 모드는 wav를 쓴다 — 로봇의 `aplay`가 mp3를 못 읽기 때문이다
(GMS는 `response_format`을 모델 단위로만 제한하므로 wav도 200. 2026-08-03 실측:
PCM 16bit mono 24kHz). mp3로 되돌리려면 `audio_sink.build_audio_sink`의
`extension`과 `potner_params.yaml`의 `speaker.audio_player`를 **함께** 바꾼다:

```bash
sudo apt install mpg123
# potner_params.yaml
#   audio_player: ["mpg123", "-q", "{text}"]
```

## 응답 지연 설계 (첫 소리 5초 보장)

직렬 체인(STT→LLM 전체→TTS 전체)은 턴당 10초를 넘겨서, 스트리밍 파이프라인으로 바꿨다
(2026-07-30 실측: **첫 소리 1.3~2.1초**, 본 답변 첫 조각 ~5초, 전체 완료는 답변 길이에 비례):

1. **필러 음성** — 서버 시작 시 "음, 잠깐만 생각해볼게요!" 등 3개를 미리 합성해두고,
   STT가 끝나는 즉시 재생. GMS 경유 Claude의 첫 토큰 지연(1.4~4.7초 변동)을 가려준다.
2. **문장 단위 TTS 파이프라이닝** — LLM 텍스트 스트림에서 문장이 완성될 때마다
   바로 TTS(동시 2개)로 보내고, 오디오 조각을 순서대로 흘려보내 이어 재생.
   `browser` 모드는 SSE로, `speaker` 모드는 `tts/play_audio`로 나간다.
3. **첫 조각 조기 절단** — 첫 문장이 길면 20자쯤에서 어절 경계로 잘라 소리부터 시작.
4. **모델** — `plant-robot-chat/.env.local`의 `ANTHROPIC_MODEL=claude-sonnet-4-6`
   (opus-4-8보다 수 배 빠름. 품질을 우선하려면 되돌리면 되지만 지연은 늘어난다).

## 구현 노트

- GMS 음성 API 호출은 **requests 필수** — stdlib urllib로는 whisper 응답 바디가
  항상 유실된다(GMS 프록시 이슈, 실측).
- GMS allowlist: STT는 `whisper-1`만, TTS는 `gpt-4o-mini-tts`만 허용 (2026-07 기준).
  allowlist는 **모델 단위**라 `response_format`은 자유롭다 — mp3/wav 둘 다 200.
- GMS가 주는 wav는 길이를 미리 몰라 RIFF/data 크기가 `0xFFFFFFFF`로 온다.
  `aplay`는 EOF까지 읽어서 이대로도 재생되지만, 파일로 쓸 때 실제 크기로
  패치한다(`audio_sink.patch_wav_sizes`) — 거짓 크기는 다른 도구가 재생 시간을
  잘못 계산하고 진단할 때 사람을 헷갈리게 한다.
- iOS Safari는 MediaRecorder가 `audio/mp4`를 내보내는데 whisper가 그대로 받는다.
