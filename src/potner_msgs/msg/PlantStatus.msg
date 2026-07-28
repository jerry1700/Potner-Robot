# 식물과 로봇의 현재 상태를 한 묶음으로 전달합니다.
# mqtt_bridge 가 이걸 그대로 JSON으로 바꿔 서버에 올립니다.

std_msgs/Header header

float32 soil_moisture      # 토양 수분 (%) — 정전식 센서 + ADS1115
float32 illuminance        # 조도 (lux) — BH1750
float32 temperature        # 대기 온도 (섭씨)
float32 battery_percent    # 배터리 잔량 (%)
