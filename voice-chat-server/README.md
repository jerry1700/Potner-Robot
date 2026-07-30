# 오린카 음성 대화 서버

휴대폰 브라우저(같은 Wi-Fi)로 로봇에 접속해 **음성으로 대화**하는 로컬 웹서버.

```
🎤 녹음 (MediaRecorder) → POST /api/voice-chat
   → STT (GMS whisper-1)
   → LLM (potner_llm DialogueService — tool use·페르소나·히스토리 그대로)
   → TTS (GMS gpt-4o-mini-tts)
   → mp3 자동 재생
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
| `GET /api/health` | 서버·LLM 상태 |
| `POST /api/voice-chat` | `audio`(파일) + `session_id`(폼) → `{user_text, reply_text, audio_b64}` |
| `POST /api/session/end` | 대화 히스토리 저장 + 맥락 초기화 (페이지 이탈 시 자동 호출) |

세션은 `session_id`별로 대화 맥락이 유지되고, 종료 시 `src/potner_llm/data/`의
conversation 백엔드에 저장돼 다음 접속에서 이어진다.

## 실제 센서 연결

지금은 `app.py`의 `_demo_status()`(고정 상태: 토양 건조)를 쓴다. 로봇에 올릴 때
MQTT/ROS에서 최신 `PlantStatus`를 돌려주는 함수로 교체하면 LLM tool use
(`get_plant_status`)가 실제 센서값으로 대답한다.

## 구현 노트

- GMS 음성 API 호출은 **requests 필수** — stdlib urllib로는 whisper 응답 바디가
  항상 유실된다(GMS 프록시 이슈, 실측).
- GMS allowlist: STT는 `whisper-1`만, TTS는 `gpt-4o-mini-tts`만 허용 (2026-07 기준).
- iOS Safari는 MediaRecorder가 `audio/mp4`를 내보내는데 whisper가 그대로 받는다.
