import logging
import cv2
import cv2.aruco as aruco

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)


def generate_markers() -> None:
    """
    1번부터 4번까지의 ArUco 마커를 생성하고, 인식률 향상을 위한 패딩을 추가하여 저장합니다.
    """
    aruco_dict = aruco.getPredefinedDictionary(aruco.DICT_6X6_250)
    marker_size = 400
    padding = 100

    logger.info("Starting generation of station markers...")

    for marker_id in range(1, 5):
        marker_image = aruco.generateImageMarker(aruco_dict, marker_id, marker_size)

        # 가장자리에 두꺼운 흰색 여백(Padding) 추가
        padded_image = cv2.copyMakeBorder(
            marker_image,
            padding,
            padding,
            padding,
            padding,
            cv2.BORDER_CONSTANT,
            value=[255, 255, 255],
        )

        filename = f"marker_id_{marker_id}.png"
        cv2.imwrite(filename, padded_image)
        logger.info(f"Marker image saved successfully: {filename}")

    logger.info(
        "Generation of all 4 station markers (Charging, Water, Sunlight, Wind) is complete."
    )


if __name__ == "__main__":
    generate_markers()
