"""plant_sensors — 화분과 배터리 상태를 읽어 발행합니다.

여기서 나온 값은 mqtt_bridge 가 서버로 올립니다. 판단하는 쪽은 서버라서
로봇은 이 값을 보고 스스로 움직이지 않습니다 — 측정과 판단이 분리되어
있습니다.

세 칩이 젯슨 I2C 하나를 공유합니다.

    BH1750  (0x23)  조도        -> plant/lux
    ADS1115 (0x48)  토양 수분   -> plant/moisture   (아날로그 -> ADC)
    INA226  (0x40)  배터리 전압 -> battery/percent

센서마다 독립적으로 다룹니다. 하나가 없어도 나머지는 계속 발행합니다.
없는 값은 **아예 발행하지 않습니다.** 가짜 값을 흘리면 서버가 그걸 저장하고
그 값으로 급수·송풍을 판단하기 때문입니다.

시작할 때 어느 주소가 응답했는지 로그에 남기므로 i2cdetect 보다 나은
진단이 됩니다.
"""

import time

import rclpy
from rclpy.node import Node
from std_msgs.msg import Float32

from potner_base.plant_conversions import (
    battery_percent,
    battery_wiring_suspect,
    lux_from_raw,
    moisture_percent,
    to_signed16,
)

try:
    from smbus2 import SMBus, i2c_msg
except ImportError:  # 센서 없는 개발 PC에서도 파일을 열어볼 수 있게
    SMBus = None
    i2c_msg = None

BH1750_ADDR = 0x23
BH1750_POWER_ON = 0x01
BH1750_ONE_TIME_HIRES = 0x20
BH1750_MEASURE_DELAY = 0.18  # 데이터시트 최대 측정 시간

ADS1115_ADDR = 0x48
ADS1115_CONFIG_REG = 0x01
ADS1115_CONVERSION_REG = 0x00
# AIN0 단일입력, 단발 측정, ±4.096V, 128SPS, 비교기 비활성
ADS1115_CONFIG_A0 = (0xC3, 0x83)
ADS1115_CONVERT_DELAY = 0.02

INA226_ADDR = 0x40
INA226_BUS_VOLTAGE_REG = 0x02
INA226_VOLTAGE_LSB = 1.25e-3  # V


class PlantSensors(Node):
    def __init__(self):
        super().__init__("plant_sensors")

        self.declare_parameter("i2c_bus", 7)  # Orin Nano 40핀 헤더
        self.declare_parameter("publish_period", 2.0)

        # 실측 보정값. 센서를 공기 중에 두고 읽은 값과 물에 담그고 읽은
        # 값입니다. 둘 다 0 이면(보정 전) moisture 를 아예 발행하지 않습니다 —
        # 가짜 값이 흘러가면 엉뚱한 급수 임무가 뜹니다.
        # potner_params.yaml 과 반드시 같아야 하며, 어긋나면
        # tests/test_config_consistency.py 가 잡아냅니다.
        self.declare_parameter("moisture_raw_dry", 24000)
        self.declare_parameter("moisture_raw_wet", 12000)

        self.declare_parameter("battery_cells", 4)  # 젯슨팩은 4S
        self.declare_parameter("battery_voltage_path", "")

        self._bus_number = self.get_parameter("i2c_bus").value
        self._raw_dry = self.get_parameter("moisture_raw_dry").value
        self._raw_wet = self.get_parameter("moisture_raw_wet").value
        self._cells = self.get_parameter("battery_cells").value
        self._voltage_path = self.get_parameter("battery_voltage_path").value

        self._lux_pub = self.create_publisher(Float32, "plant/lux", 10)
        self._moisture_pub = self.create_publisher(Float32, "plant/moisture", 10)
        self._battery_pub = self.create_publisher(Float32, "battery/percent", 10)

        self._present = self._probe()
        self.create_timer(self.get_parameter("publish_period").value, self._tick)

    # --- I2C ---

    def _probe(self):
        """어느 센서가 응답하는지 한 번 확인하고 결과를 로그에 남깁니다."""
        if SMBus is None:
            self.get_logger().warn(
                "smbus2 미설치 — 센서를 읽지 않습니다. "
                "sudo apt install python3-smbus2 또는 pip install smbus2"
            )
            return set()

        candidates = {
            "BH1750": BH1750_ADDR,
            "ADS1115": ADS1115_ADDR,
            "INA226": INA226_ADDR,
        }
        present = set()
        failures = {}

        try:
            with SMBus(self._bus_number) as bus:
                for name, address in candidates.items():
                    try:
                        # read_byte 를 쓰는 이유가 있습니다. SMBus 의
                        # write_quick(빈 쓰기)은 테그라 I2C 드라이버가
                        # 지원하지 않아서 모든 주소에서 실패합니다.
                        # i2cdetect 의 -r 옵션이 쓰는 방식과 같습니다.
                        # 세 칩 모두 읽기에 부작용이 없습니다.
                        bus.read_byte(address)
                        present.add(name)
                    except OSError as exc:
                        failures[name] = exc
        except (OSError, PermissionError) as exc:
            self.get_logger().error(
                f"I2C 버스 {self._bus_number} 를 열 수 없습니다: {exc} — "
                "버스 번호가 맞는지, i2c 그룹에 속해 있는지 확인하세요."
            )
            return set()

        for name, address in candidates.items():
            if name in present:
                self.get_logger().info(f"  {name} (0x{address:02X}) 응답")
            else:
                # 실패 이유를 함께 남깁니다. 그냥 "없음" 만 찍으면 배선
                # 문제인지 드라이버 문제인지 구분할 수 없습니다.
                self.get_logger().info(
                    f"  {name} (0x{address:02X}) 없음 — {failures.get(name)}"
                )

        if not present:
            self.get_logger().warn(
                f"I2C 버스 {self._bus_number} 에 응답하는 센서가 없습니다. "
                "배선과 버스 번호를 확인하세요."
            )
        return present

    def _read_lux(self):
        with SMBus(self._bus_number) as bus:
            # 일회성 측정 모드는 측정을 끝내면 스스로 절전 상태로 들어갑니다.
            # 그래서 매번 전원을 먼저 켜야 합니다. 이걸 빼먹으면 계속 0 이
            # 나옵니다.
            bus.write_byte(BH1750_ADDR, BH1750_POWER_ON)
            bus.write_byte(BH1750_ADDR, BH1750_ONE_TIME_HIRES)
            time.sleep(BH1750_MEASURE_DELAY)
            message = i2c_msg.read(BH1750_ADDR, 2)
            bus.i2c_rdwr(message)
            high, low = list(message)
        return lux_from_raw((high << 8) | low)

    def _read_moisture_raw(self):
        with SMBus(self._bus_number) as bus:
            bus.write_i2c_block_data(
                ADS1115_ADDR, ADS1115_CONFIG_REG, list(ADS1115_CONFIG_A0)
            )
            time.sleep(ADS1115_CONVERT_DELAY)
            high, low = bus.read_i2c_block_data(
                ADS1115_ADDR, ADS1115_CONVERSION_REG, 2
            )
        return to_signed16((high << 8) | low)

    def _read_battery_voltage(self):
        """INA226 이 있으면 그걸 쓰고, 없으면 젯슨 내장 전력 모니터를 봅니다.

        Orin Nano 는 배럴잭 입력 전압을 자체 INA3221 로 재고 있어서, 그
        값을 sysfs 로 읽으면 INA226 없이도 배터리 전압을 알 수 있습니다.
        경로는 젯팩 버전마다 달라 파라미터로 받습니다.
        """
        if "INA226" in self._present:
            with SMBus(self._bus_number) as bus:
                high, low = bus.read_i2c_block_data(
                    INA226_ADDR, INA226_BUS_VOLTAGE_REG, 2
                )
            return ((high << 8) | low) * INA226_VOLTAGE_LSB

        if self._voltage_path:
            with open(self._voltage_path, encoding="utf-8") as handle:
                return int(handle.read().strip()) / 1000.0  # mV -> V

        return None

    # --- 발행 ---

    def _tick(self):
        self._publish_lux()
        self._publish_moisture()
        self._publish_battery()

    def _publish_lux(self):
        if "BH1750" not in self._present:
            return
        try:
            self._lux_pub.publish(Float32(data=float(self._read_lux())))
        except OSError as exc:
            self.get_logger().warn(
                f"BH1750 읽기 실패: {exc}", throttle_duration_sec=10.0
            )

    def _publish_moisture(self):
        if "ADS1115" not in self._present:
            return
        try:
            raw = self._read_moisture_raw()
        except OSError as exc:
            self.get_logger().warn(
                f"ADS1115 읽기 실패: {exc}", throttle_duration_sec=10.0
            )
            return

        percent = moisture_percent(raw, self._raw_dry, self._raw_wet)
        if percent is None:
            # 보정 전에는 값을 흘리지 않습니다. 엉뚱한 급수 임무가 뜹니다.
            self.get_logger().warn(
                f"토양 수분 보정이 안 됐습니다 (원시값 {raw}). "
                "공기 중과 물속 값을 재서 moisture_raw_dry/wet 에 넣으세요.",
                throttle_duration_sec=30.0,
            )
            return

        self._moisture_pub.publish(Float32(data=float(percent)))

    def _publish_battery(self):
        try:
            voltage = self._read_battery_voltage()
        except (OSError, ValueError) as exc:
            self.get_logger().warn(
                f"배터리 전압 읽기 실패: {exc}", throttle_duration_sec=10.0
            )
            return

        if voltage is None:
            return

        if battery_wiring_suspect(voltage, self._cells):
            # battery_percent() 는 곡선 끝에서 값을 잘라 항상 0~100 을
            # 돌려주므로, 이 경고가 없으면 배선이 빠진 것도 그냥 "방전"으로
            # 보입니다. 값은 그대로 발행합니다 — mission_manager 에게는
            # 여전히 유효한 0% 근처 값입니다.
            self.get_logger().warn(
                f"배터리 전압이 비정상적으로 낮습니다 ({voltage:.2f}V, "
                f"{self._cells}S 팩). 방전이 아니라 배선(VBS)이 빠졌거나 "
                "접촉 불량일 가능성이 큽니다.",
                throttle_duration_sec=30.0,
            )

        percent = battery_percent(voltage, self._cells)
        if percent is not None:
            self._battery_pub.publish(Float32(data=float(percent)))


def main(args=None):
    rclpy.init(args=args)
    node = PlantSensors()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
