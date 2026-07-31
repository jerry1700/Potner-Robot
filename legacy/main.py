import time
import logging
from enum import IntEnum
from typing import Dict

from vision.auto_docking_vision import RobotDockingSystem
from navigation.obstacle_detector import BackgroundObstacleDetector
from navigation.wheel_motor_controller import MotorController

# 로깅 설정
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)


class StationMarker(IntEnum):
    """
    스테이션별 ArUco 마커 ID 정의.
    """

    CHARGING = 1
    WATER = 2
    SUNLIGHT = 3
    WIND = 4


# 센서 데이터 모의 객체 (Mock)
mock_sensors: Dict[str, float] = {
    "battery": 15.0,  # 20% 미만: 충전 필요
    "moisture": 25.0,  # 30% 미만: 급수 필요
    "light": 100.0,  # 200 Lux 미만: 햇빛 필요
    "temperature": 32.0,  # 30도 이상: 송풍 필요
}


class RobotMainController:
    """
    반려식물 AIoT 로봇의 메인 상태를 관리하고 스케줄링하는 컨트롤러 클래스.
    """

    def __init__(self) -> None:
        self.motor = MotorController()
        self.docking_system = RobotDockingSystem(motor_controller=self.motor)
        self.obstacle_detector = BackgroundObstacleDetector(threshold_cm=20.0)

        # 긴급 정지 콜백 등록
        self.obstacle_detector.on_obstacle_detected = self._handle_obstacle

        # 장애물 해제 시 긴급 정지 해제 (회피 주행 도입 전 임시 복구 로직)
        self.obstacle_detector.on_obstacle_cleared = self._handle_obstacle_cleared

        # 도킹 시스템에 감지기 인스턴스 공유 (도킹 중 장애물 상태 참조용)
        self.docking_system.set_obstacle_detector(self.obstacle_detector)

    def _handle_obstacle(self, distance: float) -> None:
        logger.warning(
            f"Main Controller received emergency stop signal! Obstacle at {distance:.1f}cm"
        )
        self.motor.emergency_stop()

    def _handle_obstacle_cleared(self) -> None:
        logger.info("Main Controller: Obstacle cleared, releasing emergency stop.")
        self.motor.clear_emergency_stop()

    def run(self) -> None:
        """
        메인 컨트롤 루프를 실행합니다.
        주기적으로 센서 데이터를 모니터링하고 우선순위에 따라 도킹을 수행합니다.
        """
        logger.info("System boot completed. Initializing main controller.")
        self.obstacle_detector.start()

        while True:
            logger.info("Monitoring environmental sensors... (State: IDLE)")
            time.sleep(1.0)

            # --- 우선순위 스케줄링 로직 ---
            if mock_sensors["battery"] < 20.0:
                logger.warning(
                    f"Low battery detected ({mock_sensors['battery']}%) -> Moving to charging station."
                )
                self.docking_system.start_docking(StationMarker.CHARGING)
                logger.info("Docking successful. Charging completed.")
                mock_sensors["battery"] = 100.0
                time.sleep(1.0)
                continue

            if mock_sensors["moisture"] < 30.0:
                logger.warning(
                    f"Low moisture detected ({mock_sensors['moisture']}%) -> Moving to water station."
                )
                self.docking_system.start_docking(StationMarker.WATER)
                logger.info("Docking successful. Watering completed.")
                mock_sensors["moisture"] = 100.0
                time.sleep(1.0)
                continue

            if mock_sensors["light"] < 200.0:
                logger.warning(
                    f"Low sunlight detected ({mock_sensors['light']} Lux) -> Moving to sunlight station."
                )
                self.docking_system.start_docking(StationMarker.SUNLIGHT)
                logger.info("Docking successful. Sunlight exposure completed.")
                mock_sensors["light"] = 500.0
                time.sleep(1.0)
                continue

            if mock_sensors["temperature"] >= 30.0:
                logger.warning(
                    f"High temperature detected ({mock_sensors['temperature']}C) -> Moving to wind station."
                )
                self.docking_system.start_docking(StationMarker.WIND)
                logger.info("Docking successful. Cooling completed.")
                mock_sensors["temperature"] = 25.0
                time.sleep(1.0)
                continue

            logger.info("All environmental conditions are optimal. Scenario completed.")
            break

    def shutdown(self) -> None:
        self.obstacle_detector.stop()


if __name__ == "__main__":
    controller = RobotMainController()
    try:
        controller.run()
    finally:
        controller.shutdown()
