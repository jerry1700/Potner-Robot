/*
 * Potner 저수준 모터 컨트롤러 (ESP32 DevKit V1)
 *
 * 젯슨이 못 하는 일만 여기서 합니다.
 *   - 엔코더 쿼드러처 디코딩: 안전 상한(0.25m/s)에서 초당 약 7,600카운트 x 2,
 *     무부하 최고속도면 11,700카운트 x 2. 리눅스 파이썬으로는 카운트를
 *     흘립니다. ESP32의 PCNT는 하드웨어라 CPU 개입 없이 셉니다.
 *   - 바퀴 속도 PID: 주기가 흔들리면 안 되는 제어 루프.
 *   - 20kHz PWM: 가청 대역 밖이라 모터가 조용합니다. PCA9685는 1.5kHz가
 *     한계라 낑낑 소리가 납니다.
 *
 * 젯슨과는 USB 시리얼 한 줄로만 대화합니다. 프로토콜 정의는
 * src/potner_base/potner_base/serial_protocol.py 와 반드시 일치해야 합니다.
 */

#include <Arduino.h>
#include <ESP32Encoder.h>

// ===== 핀 배치 (ESP32 DevKit V1 30핀) =====================================
// GPIO 34~39는 입력 전용이라 엔코더에 딱 맞습니다. 출력 가능한 핀을 아낍니다.
// GPIO 0, 2, 12, 15는 부팅 스트래핑 핀이라 절대 쓰지 마세요.
constexpr int PIN_L_RPWM = 25;
constexpr int PIN_L_LPWM = 26;
constexpr int PIN_R_RPWM = 32;
constexpr int PIN_R_LPWM = 33;

constexpr int PIN_L_ENC_A = 34;
constexpr int PIN_L_ENC_B = 35;
constexpr int PIN_R_ENC_A = 36;
constexpr int PIN_R_ENC_B = 39;

// ===== 하드웨어 상수 =======================================================
// ★ 실측값입니다. 사양서의 "1440 CPR" 은 채널당 사이클 수이고, ESP32Encoder
//   의 attachFullQuad() 는 한 사이클을 4카운트로 세므로 실제로는 4배입니다.
//   바퀴에 표시하고 손으로 정확히 한 바퀴 돌려 5,941카운트를 확인했습니다
//   (손 오차 3%). 예전 폭주 로그의 11,100카운트/초도 이 값으로 환산해야
//   0.36m/s 로 무부하 최고속도(122RPM=0.383m/s)와 맞아떨어집니다.
//   1440 으로 되돌리면 펌웨어가 속도를 4배로 착각해 실제로는 명령의 1/4
//   속도로만 움직입니다. src/potner_base/kinematics.py 와 반드시 같아야 합니다.
constexpr float COUNTS_PER_REV   = 5760.0f;  // 1440 CPR x 4 (쿼드러처), 실측 확인
constexpr float WHEEL_DIAMETER_M = 0.060f;   // 확정 — 60mm 구동 바퀴
constexpr float MAX_WHEEL_MPS    = 0.25f;

constexpr int PWM_FREQ_HZ   = 20000;  // 가청 대역 밖
constexpr int PWM_RESOLUTION = 10;    // 0~1023
constexpr int PWM_MAX = (1 << PWM_RESOLUTION) - 1;

constexpr int CH_L_R = 0;  // LEDC 채널
constexpr int CH_L_L = 1;
constexpr int CH_R_R = 2;
constexpr int CH_R_L = 3;

// ===== 제어 주기 ===========================================================
constexpr uint32_t CONTROL_PERIOD_MS  = 10;   // 100Hz PID
constexpr uint32_t FEEDBACK_PERIOD_MS = 33;   // 약 30Hz 로 젯슨에 보고
constexpr uint32_t WATCHDOG_TIMEOUT_MS = 500; // 젯슨이 조용하면 멈춤

// ===== PID 게인 ============================================================
// TODO: 실기에서 튜닝. 바퀴가 떨리면 kp를 낮추고, 목표 속도에 못 미치면
//       ki를 조금씩 올리세요. kd는 대개 0으로 두어도 됩니다.
constexpr float KP = 800.0f;
constexpr float KI = 1600.0f;
constexpr float KD = 0.0f;

// ===== 피드포워드 ==========================================================
// PID 만으로는 정지 상태에서 출력이 너무 천천히 오릅니다. 실측한 오도메트리
// 로그에서 목표 0.12m/s 에 도달하는 데 0.6초가 걸렸는데, 앱의 버튼 한 번이
// 그 0.6초라 가속만 하다 끝나서 2~3cm 밖에 못 갔습니다. 회전은 목표 바퀴
// 속도가 절반(0.06m/s)이라 아예 정지 마찰을 못 이겼습니다.
//
// 그래서 목표 속도로부터 필요한 duty 를 미리 얹고, PID 는 그 위에서 오차만
// 보정하게 합니다. 쿨롱 마찰(정지 돌파분) + 점성 마찰(속도 비례분) 모델입니다.
//
// 바퀴가 출발할 때 튀거나 떨리면 FF_STATIC 을 먼저 낮추세요. 0 으로 두면
// 피드포워드가 사실상 꺼져 예전 동작으로 돌아갑니다.
constexpr float FF_STATIC = 0.15f;        // 정지 마찰 돌파에 필요한 최소 duty
constexpr float FF_SLOPE = 0.85f;         // 속도 비례분
constexpr float NO_LOAD_MAX_MPS = 0.383f; // 무부하 122RPM 환산 바퀴 선속도

// 피드포워드가 정상상태 duty 를 직접 주게 됐으므로 적분은 트림만 합니다.
// 예전 ±1.0(duty ±1.6 어치)은 이제 필요 없고, 오히려 위험합니다 — 엔코더
// 선이 빠져 measured 가 0으로 얼어붙으면 적분이 끝까지 감겨 바퀴가 전속으로
// 폭주합니다(실기에서 당한 고장 모드). 권한을 좁혀 그 폭을 제한합니다.
constexpr float INTEGRAL_LIMIT = 0.15f;  // duty ±0.24 어치
// 총출력을 피드포워드 근방으로 묶는 상한. 정상 동작에서는 걸리지 않고,
// 엔코더가 죽었을 때만 duty 가 1.0 까지 가는 것을 막습니다.
constexpr float PID_HEADROOM = 0.30f;

ESP32Encoder encLeft;
ESP32Encoder encRight;

struct Wheel {
  float target_mps = 0.0f;
  float measured_mps = 0.0f;
  float integral = 0.0f;
  float prev_error = 0.0f;
  int64_t prev_count = 0;
};

Wheel wheelL;
Wheel wheelR;

uint32_t lastControlMs = 0;
uint32_t lastFeedbackMs = 0;
uint32_t lastCommandMs = 0;
String rxBuffer;

// --------------------------------------------------------------------------
uint8_t xorChecksum(const String &payload) {
  uint8_t value = 0;
  for (size_t i = 0; i < payload.length(); ++i) value ^= (uint8_t)payload[i];
  return value;
}

void applyPwm(int chForward, int chReverse, float duty) {
  duty = constrain(duty, -1.0f, 1.0f);
  int magnitude = (int)(fabs(duty) * PWM_MAX);

  if (duty > 0.001f) {
    ledcWrite(chForward, magnitude);
    ledcWrite(chReverse, 0);
  } else if (duty < -0.001f) {
    ledcWrite(chForward, 0);
    ledcWrite(chReverse, magnitude);
  } else {
    ledcWrite(chForward, 0);
    ledcWrite(chReverse, 0);
  }
}

float countsToMeters(int64_t counts) {
  return (counts / COUNTS_PER_REV) * PI * WHEEL_DIAMETER_M;
}

float feedforward(float target_mps) {
  // 목표가 0 이면 미리 얹을 것이 없습니다. 여기서 0 을 안 돌려주면 정지
  // 명령에도 바퀴가 슬금슬금 기어갑니다.
  if (target_mps == 0.0f) return 0.0f;

  float magnitude =
      FF_STATIC + FF_SLOPE * fabs(target_mps) / NO_LOAD_MAX_MPS;
  return target_mps > 0.0f ? magnitude : -magnitude;
}

float updatePid(Wheel &wheel, int64_t count, float dt) {
  float travelled = countsToMeters(count - wheel.prev_count);
  wheel.prev_count = count;
  wheel.measured_mps = travelled / dt;

  // 목표가 0이고 측정도 0이면 출력을 끊고 적분항을 비웁니다. 엔코더 선이
  // 빠지면 measured가 영원히 0이라, 주행 중 최대로 감긴 적분항이 정지
  // 명령(V,0,0)을 받아도 안 풀려서(error=0) 바퀴가 전속력으로 계속 돕니다.
  // 실기에서 실제로 당했습니다 — 엔코더 카운트는 얼어 있는데 바퀴만 도는
  // 로그가 그 증거였습니다.
  if (wheel.target_mps == 0.0f && wheel.measured_mps == 0.0f) {
    wheel.integral = 0.0f;
    wheel.prev_error = 0.0f;
    return 0.0f;
  }

  float error = wheel.target_mps - wheel.measured_mps;
  wheel.integral += error * dt;

  // 적분 와인드업 방지. 바퀴가 걸려 못 움직일 때 적분항이 무한히 커져서
  // 장애물이 치워지는 순간 로봇이 튀어나가는 걸 막습니다.
  wheel.integral = constrain(wheel.integral, -INTEGRAL_LIMIT, INTEGRAL_LIMIT);

  float derivative = (error - wheel.prev_error) / dt;
  wheel.prev_error = error;

  // 피드포워드가 정상상태를 담당하고 PID 는 그 위에서 트림만 합니다. 총출력을
  // 피드포워드 근방으로 묶어두면 엔코더가 죽어 measured 가 0으로 얼어붙어도
  // duty 가 1.0 까지 가지 않습니다. 정상 동작에서는 이 상한에 안 걸립니다.
  float ff = feedforward(wheel.target_mps);
  float pid = (KP * error + KI * wheel.integral + KD * derivative) / 1000.0f;
  float ceiling = fabs(ff) + PID_HEADROOM;
  return constrain(ff + pid, -ceiling, ceiling);
}

void stopAll() {
  wheelL.target_mps = 0.0f;
  wheelR.target_mps = 0.0f;
  wheelL.integral = 0.0f;
  wheelR.integral = 0.0f;
  applyPwm(CH_L_R, CH_L_L, 0.0f);
  applyPwm(CH_R_R, CH_R_L, 0.0f);
}

// --------------------------------------------------------------------------
void handleLine(const String &line) {
  int star = line.lastIndexOf('*');
  if (star < 0) return;

  String payload = line.substring(0, star);
  uint8_t received = (uint8_t)strtol(line.substring(star + 1).c_str(), nullptr, 16);
  if (received != xorChecksum(payload)) return;  // 깨진 프레임은 조용히 버림

  if (payload.startsWith("S")) {
    stopAll();
    lastCommandMs = millis();
    return;
  }

  if (payload.startsWith("V,")) {
    int firstComma = payload.indexOf(',');
    int secondComma = payload.indexOf(',', firstComma + 1);
    if (secondComma < 0) return;

    float left = payload.substring(firstComma + 1, secondComma).toFloat();
    float right = payload.substring(secondComma + 1).toFloat();

    wheelL.target_mps = constrain(left, -MAX_WHEEL_MPS, MAX_WHEEL_MPS);
    wheelR.target_mps = constrain(right, -MAX_WHEEL_MPS, MAX_WHEEL_MPS);
    lastCommandMs = millis();
  }
}

void readSerial() {
  while (Serial.available()) {
    char c = (char)Serial.read();
    if (c == '\n') {
      handleLine(rxBuffer);
      rxBuffer = "";
    } else if (c != '\r' && rxBuffer.length() < 96) {
      rxBuffer += c;
    }
  }
}

void sendFeedback() {
  // TODO: 범퍼 ToF(VL53L0X)를 달면 실제 거리로 교체. 지금은 최대값 고정.
  int leftBumperMm = 1200;
  int rightBumperMm = 1200;

  String payload = "O," + String((long)encLeft.getCount()) +
                   "," + String((long)encRight.getCount()) +
                   "," + String(leftBumperMm) +
                   "," + String(rightBumperMm) +
                   "," + String(millis());

  char checksum[8];
  snprintf(checksum, sizeof(checksum), "*%02X", xorChecksum(payload));
  Serial.println(payload + checksum);
}

// --------------------------------------------------------------------------
void setup() {
  Serial.begin(115200);

  ledcSetup(CH_L_R, PWM_FREQ_HZ, PWM_RESOLUTION);
  ledcSetup(CH_L_L, PWM_FREQ_HZ, PWM_RESOLUTION);
  ledcSetup(CH_R_R, PWM_FREQ_HZ, PWM_RESOLUTION);
  ledcSetup(CH_R_L, PWM_FREQ_HZ, PWM_RESOLUTION);

  ledcAttachPin(PIN_L_RPWM, CH_L_R);
  ledcAttachPin(PIN_L_LPWM, CH_L_L);
  ledcAttachPin(PIN_R_RPWM, CH_R_R);
  ledcAttachPin(PIN_R_LPWM, CH_R_L);

  // GPIO 34~39에는 내부 풀업이 없습니다. 레벨 시프터 쪽에서 확실히
  // 구동해 주지 않으면 값이 떠서 카운트가 튑니다.
  encLeft.attachFullQuad(PIN_L_ENC_A, PIN_L_ENC_B);
  encRight.attachFullQuad(PIN_R_ENC_A, PIN_R_ENC_B);
  encLeft.clearCount();
  encRight.clearCount();

  stopAll();
  lastCommandMs = millis();
}

void loop() {
  readSerial();

  uint32_t now = millis();

  // 워치독: 젯슨이 죽거나 USB가 빠져도 로봇이 계속 달리면 안 됩니다.
  if (now - lastCommandMs > WATCHDOG_TIMEOUT_MS) {
    stopAll();
  }

  if (now - lastControlMs >= CONTROL_PERIOD_MS) {
    float dt = (now - lastControlMs) / 1000.0f;
    lastControlMs = now;

    applyPwm(CH_L_R, CH_L_L, updatePid(wheelL, encLeft.getCount(), dt));
    applyPwm(CH_R_R, CH_R_L, updatePid(wheelR, encRight.getCount(), dt));
  }

  if (now - lastFeedbackMs >= FEEDBACK_PERIOD_MS) {
    lastFeedbackMs = now;
    sendFeedback();
  }
}
