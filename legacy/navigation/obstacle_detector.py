import time
import threading
import logging
from typing import Callable, Optional

from sensors.ultrasonic import get_distance as get_ultrasonic_distance

logger = logging.getLogger(__name__)


class BackgroundObstacleDetector:
    """
    백그라운드 스레드에서 지속적으로 초음파 센서를 모니터링하여 장애물을 감지합니다.
    일반 주행 및 도킹 중에도 독립적으로 동작하며, 장애물 감지/해제 시 콜백을 발생시킵니다.
    """

    def __init__(self, threshold_cm: float = 20.0, check_interval: float = 0.1):
        self.threshold_cm = threshold_cm
        self.check_interval = check_interval
        self._running = False
        self._thread: Optional[threading.Thread] = None

        self.is_obstacle_detected = False
        self.current_distance = 999.0

        # 콜백 함수들
        self.on_obstacle_detected: Optional[Callable[[float], None]] = None
        self.on_obstacle_cleared: Optional[Callable[[], None]] = None

    def start(self) -> None:
        """장애물 감지 백그라운드 스레드를 시작합니다."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._monitor_loop, daemon=True, name="ObstacleDetectorThread"
        )
        self._thread.start()
        logger.info("Background obstacle detector thread started.")

    def stop(self) -> None:
        """장애물 감지 백그라운드 스레드를 중지합니다."""
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join()
        logger.info("Background obstacle detector thread stopped.")

    def _monitor_loop(self) -> None:
        while self._running:
            try:
                self.current_distance = get_ultrasonic_distance()

                if self.current_distance < self.threshold_cm:
                    if not self.is_obstacle_detected:
                        self.is_obstacle_detected = True
                        logger.warning(
                            f"[ObstacleDetector] Obstacle detected at {self.current_distance:.1f}cm!"
                        )
                        if self.on_obstacle_detected:
                            self.on_obstacle_detected(self.current_distance)
                else:
                    if self.is_obstacle_detected:
                        # 약간의 히스테리시스(Hysteresis)를 주어 경계값에서 깜빡임 방지 (예: threshold + 5cm 이상일 때 해제)
                        if self.current_distance > self.threshold_cm + 5.0:
                            self.is_obstacle_detected = False
                            logger.info(
                                f"[ObstacleDetector] Obstacle cleared. Current distance: {self.current_distance:.1f}cm"
                            )
                            if self.on_obstacle_cleared:
                                self.on_obstacle_cleared()
            except Exception as e:
                logger.error(f"Error in obstacle detection loop: {e}")

            time.sleep(self.check_interval)
