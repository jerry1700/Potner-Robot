# ESP32가 올려보낸 바퀴 원시 데이터. 오도메트리 디버깅용입니다.
# 바퀴가 헛도는지, 엔코더 한쪽이 죽었는지 여기서 바로 보입니다.

std_msgs/Header header

int32 left_ticks           # 좌 엔코더 누적 카운트 (5760 = 1회전, 실측)
int32 right_ticks          # 우 엔코더 누적 카운트
float32 left_velocity      # 좌 바퀴 실측 선속도 (m/s)
float32 right_velocity     # 우 바퀴 실측 선속도 (m/s)
float32 left_target        # 좌 바퀴 목표 선속도 (m/s)
float32 right_target       # 우 바퀴 목표 선속도 (m/s)
uint32 board_millis        # ESP32 부팅 후 경과 시간 (ms)
