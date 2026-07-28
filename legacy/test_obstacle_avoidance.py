import sys
import os
import time
import logging

# 상위 폴더의 모듈을 import 하기 위해 path 추가
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from navigation.wheel_motor_controller import MotorController
from navigation.obstacle_detector import BackgroundObstacleDetector

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)


def run_test():
    logger.info("=== 🚙 장애물 감지 및 회피 기동 테스트 시작 ===")
    logger.info(
        "주의: 로봇이 실제로 움직일 수 있습니다. 바퀴가 바닥에 닿아있다면 넓은 공간에서 테스트하세요!"
    )

    motor = MotorController()
    detector = BackgroundObstacleDetector(threshold_cm=20.0, check_interval=0.1)

    def on_detect(distance):
        logger.warning(
            f"[센서 알림] 장애물 감지! (거리: {distance:.1f}cm) 긴급 정지 발동!"
        )
        motor.emergency_stop()

    def on_clear():
        logger.info("[센서 알림] 장애물이 사라졌습니다.")

    detector.on_obstacle_detected = on_detect
    detector.on_obstacle_cleared = on_clear

    # [모킹 로직] 실제 센서가 없으므로 테스트용으로 5초 뒤에 가짜 장애물을 발생시킵니다.
    def mock_distance_generator():
        start_time = time.time()
        while True:
            elapsed = time.time() - start_time
            # 5초~10초 사이에만 10cm 반환, 그 외엔 999cm (안전)
            if 5.0 < elapsed < 10.0:
                yield 10.0
            else:
                yield 999.0

    mock_gen = mock_distance_generator()

    # 이미 import된 모듈 내부의 참조를 직접 변경해야 정상적으로 가짜 센서값이 주입됩니다.
    import navigation.obstacle_detector

    navigation.obstacle_detector.get_ultrasonic_distance = lambda: next(mock_gen)

    detector.start()

    try:
        logger.info("기본 주행 모드: 직진 (5초 뒤에 가상의 장애물이 나타납니다!)")
        tick_count = 0
        while True:
            if not motor.emergency_stop_active:
                # 정상 주행 속도를 50%로 대폭 상향
                motor.send_command(linear_speed=0.50, angular_speed=0.0)

                # 1초(0.1s * 10)마다 현재 상태 출력
                if tick_count % 10 == 0:
                    logger.info(
                        f"[주행 중] 전진 속도: 50%, 현재 센서(모의) 거리: {detector.current_distance:.1f}cm"
                    )
                tick_count += 1

                time.sleep(0.1)
            else:
                # 장애물이 감지되어 긴급 정지 상태가 됨 -> 회피 기동 시작
                logger.info("--- 🚨 회피 기동 시퀀스 시작 ---")

                # 1단계: 후진 (-50%)
                logger.info("Step 1: 후진 (1.5초) - 속도: -50%")
                motor.send_command(linear_speed=-0.50, angular_speed=0.0, force=True)
                time.sleep(1.5)

                # 2단계: 우회전 (조향각 +80%)
                logger.info("Step 2: 제자리 우회전 (최소 1.5초) - 조향각: +80%")
                while True:
                    motor.send_command(linear_speed=0.0, angular_speed=0.8, force=True)

                    # 1.5초 동안 0.5초 간격으로 상태 출력
                    for _ in range(3):
                        time.sleep(0.5)
                        logger.info(
                            f" -> [우회전 중] 현재 센서(모의) 거리: {detector.current_distance:.1f}cm"
                        )

                    if detector.current_distance > 40.0:
                        logger.info(
                            f"-> 전방 공간 확보됨! (최종 거리: {detector.current_distance:.1f}cm)"
                        )
                        break
                    logger.warning("-> 아직 막혀있음. 계속 우회전...")

                # 3단계: 직진 (60%)
                logger.info("Step 3: 장애물 옆으로 우회 직진 (2.0초) - 속도: 60%")
                motor.send_command(linear_speed=0.60, angular_speed=0.0, force=True)
                time.sleep(2.0)

                # 4단계: 좌회전 복귀 (-80%)
                logger.info("Step 4: 원래 방향으로 좌회전 복귀 (1.5초) - 조향각: -80%")
                motor.send_command(linear_speed=0.0, angular_speed=-0.8, force=True)
                time.sleep(1.5)

                # 회피 완료
                logger.info("--- ✅ 회피 기동 완료. 긴급 정지 해제 및 스캔 복귀 ---")
                motor.clear_emergency_stop()

    except KeyboardInterrupt:
        logger.info("사용자에 의해 테스트가 강제 종료되었습니다 (Ctrl+C).")
    finally:
        detector.stop()
        motor.stop()
        logger.info("모터 정지 및 안전 종료 완료.")


if __name__ == "__main__":
    run_test()
