import logging
import time
from typing import Optional

import cv2
import cv2.aruco as aruco
import numpy as np
import torch
from ultralytics import YOLO

from navigation.wheel_motor_controller import MotorController
from sensors.ultrasonic import get_distance as get_ultrasonic_distance

logger = logging.getLogger(__name__)


class RobotDockingSystem:
    """
    비전 기반 자율 도킹 및 초음파 센서 기반 장애물 회피 시스템 클래스.
    """

    def __init__(self, motor_controller: Optional[MotorController] = None) -> None:
        logger.info("Loading YOLOv11 Nano model...")
        self.yolo_model = YOLO("yolo11n.pt")

        self.aruco_dict = aruco.getPredefinedDictionary(aruco.DICT_6X6_250)
        self.parameters = aruco.DetectorParameters()
        self.detector = aruco.ArucoDetector(self.aruco_dict, self.parameters)

        self.cap: Optional[cv2.VideoCapture] = None
        self.center_x: int = 320

        # 비전 기반 거리 측정을 위한 핀홀 카메라 모델 상수
        self.focal_length_px: float = 600.0
        self.real_marker_width_cm: float = 5.0

        # solvePnP를 위한 가상의 카메라 매트릭스 (해상도 640x480 기준)
        self.camera_matrix = np.array(
            [
                [self.focal_length_px, 0.0, 320.0],
                [0.0, self.focal_length_px, 240.0],
                [0.0, 0.0, 1.0],
            ],
            dtype=np.float32,
        )
        self.dist_coeffs = np.zeros((4, 1))

        # P-Control 상수
        self.Kp_angular: float = 0.002
        self.Kp_yaw: float = 0.015  # 근거리 회전각 정렬을 위한 게인값
        self.target_distance_cm: float = 15.0

        self.motor = motor_controller if motor_controller else MotorController()
        self.obstacle_detector = None

    def set_obstacle_detector(self, detector) -> None:
        """백그라운드 장애물 감지기 인스턴스를 주입받습니다."""
        self.obstacle_detector = detector

    def estimate_distance_and_angle(self, target_corners: np.ndarray):
        """
        cv2.solvePnP를 사용하여 마커까지의 거리(Z)와 회전각(Yaw)을 계산합니다.
        반환값: (거리 cm, 회전각 degree)
        """
        marker_size = self.real_marker_width_cm
        # 마커의 3D 좌표 (중심이 0,0,0)
        obj_points = np.array(
            [
                [-marker_size / 2, marker_size / 2, 0],
                [marker_size / 2, marker_size / 2, 0],
                [marker_size / 2, -marker_size / 2, 0],
                [-marker_size / 2, -marker_size / 2, 0],
            ],
            dtype=np.float32,
        )

        success, rvec, tvec = cv2.solvePnP(
            obj_points,
            target_corners,
            self.camera_matrix,
            self.dist_coeffs,
            flags=cv2.SOLVEPNP_IPPE_SQUARE,
        )

        if success:
            distance = tvec[2][0]

            # 회전 벡터(rvec)를 회전 행렬로 변환
            rmat, _ = cv2.Rodrigues(rvec)
            # 마커가 카메라를 정면으로 바라볼 때를 기준으로 한 좌우 틀어짐 각도 (Yaw)
            # rmat[0, 2]와 rmat[2, 2]를 통해 Y축 기준 회전을 구합니다.
            yaw_rad = np.arctan2(rmat[0, 2], rmat[2, 2])
            yaw_deg = np.degrees(yaw_rad)

            # 마커 정면이 카메라를 향할 때 180도 부근이 나오므로, 오차를 0을 기준으로 정규화
            if yaw_deg > 90:
                yaw_error = yaw_deg - 180
            elif yaw_deg < -90:
                yaw_error = yaw_deg + 180
            else:
                yaw_error = yaw_deg

            return float(distance), float(yaw_error)

        return 999.0, 0.0

    def start_docking(self, target_marker_id: int) -> None:
        """
        주어진 마커 ID를 탐색하여 자율 도킹을 수행하며, 전방 초음파 센서를 기반으로 장애물을 우회합니다.

        Args:
            target_marker_id (int): 도킹 목표물의 ArUco 마커 ID
        """
        logger.info(
            f"Initiating docking sequence for station marker ID: {target_marker_id}"
        )

        # Linux(오린카) 환경에서는 0번 카메라(또는 /dev/video0)를 주로 사용하며, DSHOW 옵션은 윈도우 전용이므로 제거합니다.
        self.cap = cv2.VideoCapture(0)
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

        # 상태 머신 변수들
        state = "SCANNING"
        avoidance_step = 0
        avoidance_start_time = 0.0

        while state != "DOCKED":
            ret, frame = self.cap.read()
            if not ret:
                logger.error("카메라 프레임을 읽을 수 없습니다.")
                break

            # ----------------------------------------------------
            # 1. 초음파 센서 기반 장애물 감지 (최우선 순위)
            # ----------------------------------------------------
            if self.obstacle_detector:
                ultrasonic_dist = self.obstacle_detector.current_distance
                is_obstacle = self.obstacle_detector.is_obstacle_detected
            else:
                ultrasonic_dist = get_ultrasonic_distance()
                is_obstacle = ultrasonic_dist < 20.0

            # 전방 20cm 이내에 물리적 장애물이 발견되면 즉시 우회(AVOIDING) 상태로 전환
            if is_obstacle and state not in ["AVOIDING", "DOCKED"]:
                logger.warning(
                    f"Obstacle detected at {ultrasonic_dist:.1f}cm. Initiating avoidance maneuver."
                )
                state = "AVOIDING"
                avoidance_step = 1
                avoidance_start_time = time.time()

            # ----------------------------------------------------
            # 2. 장애물 우회(매크로) 상태 머신
            # ----------------------------------------------------
            if state == "AVOIDING":
                elapsed = time.time() - avoidance_start_time

                if avoidance_step == 1:
                    # 1단계: 1.5초간 뒤로 물러나기 (반려식물이 흔들리지 않게 아주 천천히)
                    self.motor.send_command(
                        linear_speed=-0.15, angular_speed=0.0, force=True
                    )
                    if elapsed > 1.5:
                        avoidance_step = 2
                        avoidance_start_time = time.time()

                elif avoidance_step == 2:
                    # 2단계: 1.5초간 크게 우회전 (장애물 비켜가기)
                    self.motor.send_command(
                        linear_speed=0.0, angular_speed=0.6, force=True
                    )
                    if elapsed > 1.5:
                        # 회전 후 초음파 앞이 뚫려있는지 확인 (40cm 이상 확보 시)
                        dist_check = (
                            self.obstacle_detector.current_distance
                            if self.obstacle_detector
                            else get_ultrasonic_distance()
                        )
                        if dist_check > 40.0:
                            avoidance_step = 3
                            avoidance_start_time = time.time()
                        else:
                            # 여전히 막혀있으면 회전 시간 연장 (다시 2단계 처음부터)
                            avoidance_start_time = time.time()

                elif avoidance_step == 3:
                    # 3단계: 2초간 직진하여 장애물을 완전히 옆으로 지나침
                    self.motor.send_command(
                        linear_speed=0.25, angular_speed=0.0, force=True
                    )
                    if elapsed > 2.0:
                        avoidance_step = 4
                        avoidance_start_time = time.time()

                elif avoidance_step == 4:
                    # 4단계: 1.5초간 좌회전하여 원래 주행하던 방향으로 복귀 시도
                    self.motor.send_command(
                        linear_speed=0.0, angular_speed=-0.6, force=True
                    )
                    if elapsed > 1.5:
                        logger.info(
                            "Obstacle avoidance completed. Resuming target scan."
                        )
                        # 회피 기동이 끝났으므로 강제 정지 플래그를 해제하고 스캔 재개
                        self.motor.clear_emergency_stop()
                        state = "SCANNING"

                # AVOIDING 상태일 때는 아래의 마커 추적 로직을 건너뜁니다.
                # cv2.putText(frame, "AVOIDING OBSTACLE...", (20, 50), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 0, 255), 3)
                # cv2.imshow("Docking Vision", frame)
                # if cv2.waitKey(1) & 0xFF == ord('q'):
                #     break
                continue

            # ----------------------------------------------------
            # 3. 비전 기반 마커 추적 및 도킹
            # ----------------------------------------------------
            # 도킹 중(APPROACHING)일 때는 반응 속도를 극한으로 끌어올리기 위해 무거운 YOLO 연산을 잠시 건너뜁니다!
            if state != "APPROACHING":
                device = "cuda" if torch.cuda.is_available() else "cpu"
                results = self.yolo_model.predict(frame, verbose=False, device=device)
                for r in results:
                    for box in r.boxes:
                        cls_id = int(box.cls[0])
                        conf = float(box.conf[0])

                        # 사람(0), 고양이(15), 개(16) 등 주요 동적 장애물 식별
                        if cls_id in [0, 15, 16] and conf > 0.5:
                            x1, y1, x2, y2 = map(int, box.xyxy[0])
                            w = x2 - x1
                            h = y2 - y1

                            # 바운딩 박스가 일정 크기 이상(가까이 있음)이면 비전 장애물로 간주하여 우회
                            if w > 250 or h > 200:
                                cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 0, 255), 3)
                                cv2.putText(
                                    frame,
                                    "WARNING: DYNAMIC OBSTACLE",
                                    (x1, y1 - 10),
                                    cv2.FONT_HERSHEY_SIMPLEX,
                                    0.6,
                                    (0, 0, 255),
                                    2,
                                )

                                if state not in ["AVOIDING", "DOCKED"]:
                                    logger.warning(
                                        f"Dynamic obstacle (Class {cls_id}) detected visually. Initiating avoidance."
                                    )
                                    state = "AVOIDING"
                                    avoidance_step = 1
                                    avoidance_start_time = time.time()
                            else:
                                cv2.rectangle(frame, (x1, y1), (x2, y2), (255, 0, 0), 2)
                                cv2.putText(
                                    frame,
                                    "OBJECT DETECTED",
                                    (x1, y1 - 10),
                                    cv2.FONT_HERSHEY_SIMPLEX,
                                    0.5,
                                    (255, 0, 0),
                                    2,
                                )

            # ArUco 마커 탐지
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            corners, ids, _ = self.detector.detectMarkers(gray)

            marker_found = False
            target_corners = None

            if ids is not None:
                for i, m_id in enumerate(ids.flatten()):
                    if m_id == target_marker_id:
                        marker_found = True
                        target_corners = corners[i][0]
                        break

            if not marker_found:
                if state == "SCANNING":
                    # 마커가 안 보이면 핸들을 꺾은 채로 아주 천천히 전진하며 탐색 (조향형 차량)
                    self.motor.send_command(linear_speed=0.12, angular_speed=0.5)
                elif state == "APPROACHING":
                    self.motor.send_command(linear_speed=0.0, angular_speed=0.0)
                    logger.warning("Target marker lost. Reverting to scanning mode.")
                    state = "SCANNING"
            else:
                state = "APPROACHING"
                # 중심점 및 오차 계산
                cx = int(
                    (
                        target_corners[0][0]
                        + target_corners[1][0]
                        + target_corners[2][0]
                        + target_corners[3][0]
                    )
                    / 4
                )
                error_x = cx - self.center_x

                # 거리 및 마커 회전각(Pose) 계산
                current_distance, yaw_error = self.estimate_distance_and_angle(
                    target_corners
                )
                logger.info(
                    f"Marker detected. Dist: {current_distance:.1f}cm, X_Err: {error_x}px, Yaw_Err: {yaw_error:.1f}deg"
                )

                # 도킹 성공(DOCKED) 판정 로직: 거리, 좌우 오차, 회전각 오차가 모두 임계치 이내일 때만 성공
                is_close_enough = current_distance <= self.target_distance_cm
                is_centered = abs(error_x) < 30  # 픽셀 오차 허용치 (예: 30px 이내)
                is_parallel = abs(yaw_error) < 10.0  # 각도 오차 허용치 (예: 10도 이내)

                if is_close_enough:
                    if is_centered and is_parallel:
                        self.motor.send_command(linear_speed=0.0, angular_speed=0.0)
                        logger.info(
                            f"✨ Docking Complete! Dist: {current_distance:.1f}cm, X_Err: {error_x}px, Yaw_Err: {yaw_error:.1f}deg"
                        )
                        state = "DOCKED"
                    else:
                        logger.warning(
                            f"거리 도달({current_distance:.1f}cm)했으나 정렬 실패 (X_Err: {error_x}px, Yaw_Err: {yaw_error:.1f}deg). 미세 조정 중..."
                        )
                        # 제자리에서 미세 조향만 수행
                        angular_speed = (error_x * self.Kp_angular) + (
                            yaw_error * self.Kp_yaw
                        )
                        self.motor.send_command(
                            linear_speed=0.0, angular_speed=angular_speed
                        )
                else:
                    # 원거리에서는 화면 중심(X)을 맞추는데 집중하고,
                    # 40cm 이내 근거리로 진입하면 평행 주차를 위해 마커의 회전각(Yaw)도 보정합니다.
                    angular_speed = error_x * self.Kp_angular

                    if current_distance < 40.0:
                        angular_speed += yaw_error * self.Kp_yaw

                    # 부드럽게 주행 (0.4)
                    self.motor.send_command(
                        linear_speed=0.4, angular_speed=angular_speed
                    )

            # --- 디버깅용 화면 출력 (원격 접속 시 에러가 나므로 주석 처리) ---
            if ids is not None:
                aruco.drawDetectedMarkers(frame, corners, ids)
            # cv2.imshow("Docking Vision", frame)

            # if cv2.waitKey(1) & 0xFF == ord('q'):
            #     logger.info("사용자에 의해 도킹이 강제 종료되었습니다.")
            #     break

        # 자원 해제
        if self.cap is not None:
            self.cap.release()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    system = RobotDockingSystem()
    system.start_docking(target_marker_id=2)
