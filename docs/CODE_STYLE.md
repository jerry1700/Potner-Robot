# 코드 스타일

팀 안에서 코드가 한 사람이 쓴 것처럼 읽히도록 정한 규칙입니다.
분량이 적은 이유는, 지키지 않을 규칙을 적어두면 아무 의미가 없기 때문입니다.

## 문자열

**f-string 만 씁니다.** `%` 서식과 `.format()` 은 쓰지 않습니다.

```python
self.get_logger().info(f"도킹 시작 (마커 {marker_id})")
raise ProtocolError(f"필드 개수가 6이 아님: {len(fields)}개")
```

## 타입 힌트

**순수 로직 모듈의 공개 함수에만** 붙입니다. `kinematics`, `serial_protocol`,
`priority`, `session`, `approach_controller`, `marker_pose` 가 여기 해당합니다.
이 함수들은 다른 패키지가 호출하는 경계면이고 단위 테스트가 붙어 있습니다.

노드 안의 내부 메서드에는 붙이지 않습니다. 다만 **ROS 콜백의 메시지
인자는 예외**로 타입을 적습니다. 어떤 메시지가 오는지가 코드에서 드러나지
않기 때문입니다.

```python
def twist_to_wheel_speeds(linear: float, angular: float, cfg: DriveConfig):
    ...

def _on_scan(self, msg: LaserScan):   # 콜백은 메시지 타입만
    ...

def _publish_odom(self, stamp):        # 내부 메서드는 생략
    ...
```

## docstring

한 줄 요약으로 시작합니다. 더 설명할 게 있으면 빈 줄을 두고 이어 씁니다.

`Args:` / `Returns:` 는 **순수 로직 공개 함수에서 인자 의미가 이름만으로
드러나지 않을 때만** 씁니다. 단위가 헷갈리는 경우(px 인지 m 인지)가
대표적입니다. 노드 메서드에는 쓰지 않습니다.

## 주석

**'무엇'이 아니라 '왜'만 적습니다.** 코드가 이미 말하는 것을 반복하지
않습니다.

```python
# 나쁨 — 코드를 그대로 옮겼을 뿐
# 좌우 바퀴 속도를 계산한다
left, right = twist_to_wheel_speeds(...)

# 좋음 — 코드가 말하지 않는 이유
# 한쪽만 자르면 로봇이 의도치 않은 방향으로 휩니다.
if peak > cfg.max_wheel_speed:
    ...
```

특히 **한 번 당한 함정은 반드시 남깁니다.** 이런 주석이 이 저장소에서
가장 값어치가 있습니다.

```python
# solvePnP 는 정면 마커에 대해 180도 근처를 돌려줍니다. 이 정규화를
# 빼먹으면 정렬 판정이 영원히 성립하지 않습니다. 실제로 당했습니다.
```

## 파일 안 순서

노드 파일은 아래 순서를 따릅니다.

```
모듈 docstring  →  import  →  상수  →  클래스
클래스 안: __init__  →  콜백  →  주요 동작  →  발행  →  보조
```

섹션이 네 개를 넘을 때만 구분선을 넣습니다. 형태는 이것으로 통일합니다.

```python
# --- 콜백 ---
```

## 모듈 docstring 첫 줄

노드 파일은 `실행이름 — 한 줄 설명` 형태로 시작합니다. `ros2 node list` 에
뜨는 이름과 파일을 바로 잇기 위한 것입니다.

```python
"""base_driver — 젯슨과 ESP32를 잇는 유일한 노드."""
```

순수 로직 모듈은 그냥 무엇을 하는 모듈인지 씁니다.

## 검사

```bash
pytest tests/ -v
flake8 . --select=E9,F63,F7,F82 --exclude=venv,build,install,log,.pio
```

젠킨스가 같은 검사를 돌립니다. 서식 자동 정렬 도구(black 등)는 쓰지
않습니다. 한글 주석 정렬이 깨지는 경우가 있어 손으로 맞춥니다.
