"""발행 주기 동안의 측정값을 모아 평균 하나로 만드는 순수 로직.

ROS 에 의존하지 않으므로 CI 에서 검증됩니다.

왜 필요한가 — 서버는 조도를 **좌측 구형 적분**으로 누적합니다. 표본 하나가
"다음 표본이 올 때까지 그 값을 유지했다"고 보고 곱합니다.

    gap_seconds     = LEAST(다음_측정시각 - 이_측정시각, max_gap_seconds)
    누적광량(lux·h)  = sum(measured_value * gap_seconds) / 3600

그런데 로봇은 2초마다 재고 10초마다 보냅니다. 예전에는 새 값이 올 때마다
덮어써서 **5개 중 4개를 버리고 마지막 하나만** 보냈습니다. 서버는 그 순간값
하나를 10초 내내 유지된 것으로 적분하므로, 순간적인 사건(로봇이 회전해
센서가 창을 향한 순간, 그림자, 얼굴 LCD 반사)이 **10초치 광량으로 5배
증폭**됩니다. 구간 평균을 보내면 좌측 구형 적분이 사실상 평균값 적분이
되어 그 증폭이 사라집니다.

★ 측정 시각으로 **창의 첫 표본 시각**을 씁니다. 마지막 표본 시각이 아닙니다.

  서버는 보고된 시각부터 다음 보고 시각까지 그 값을 유지합니다. 창
  [t0, t1) 의 평균을 t0 에 보고하면 유지 구간과 평균 구간이 정확히 겹쳐
  적분이 참값과 같아집니다. 마지막 표본 시각을 쓰면 그 평균이 **다음
  창에** 적용되어 한 창만큼 밀립니다.

한계 하나 — 서버의 일조시간은 ``>= 500 lux`` 이분 판정이라 평균으로는
완전히 복원되지 않습니다. 10초 중 5초가 900lux, 5초가 100lux 면 평균이
500 이 되어 10초 전부가 "빛 있음"으로 셉니다. 마지막값 방식보다는 낫지만
정확하진 않습니다. 더 정확히 하려면 발행 주기를 줄이거나 초과 시간 비율을
별도 필드로 보내야 하는데, 지금 프로토콜에 그 자리가 없습니다.
"""

from dataclasses import dataclass, field


@dataclass
class SensorWindow:
    """한 발행 주기 동안 들어온 측정값을 누적합니다.

    시각 타입을 가리지 않습니다 — ``datetime`` 이든 float 이든 그대로 들고
    있다가 첫 표본의 것을 돌려줍니다. 뺄셈이나 비교를 하지 않기 때문입니다.
    """

    _count: int = field(default=0, repr=False)
    _total: float = field(default=0.0, repr=False)
    _first_at: object = field(default=None, repr=False)

    @property
    def count(self) -> int:
        return self._count

    def add(self, value: float, measured_at) -> None:
        """측정값 하나를 창에 넣습니다."""
        if self._count == 0:
            self._first_at = measured_at
        self._count += 1
        self._total += float(value)

    def take(self):
        """창을 닫고 ``(평균, 첫 표본 시각)`` 을 돌려준 뒤 비웁니다.

        표본이 하나도 없으면 ``None`` 입니다 — 그때는 **아무것도 보내지
        않아야** 합니다. 값이 없는데 뭔가 보내면 서버의 ``covered_seconds``
        가 실제보다 높게 잡혀서, 데이터가 부족한 날에도 판정이 나옵니다.
        센서가 빠져 있을 때 조용히 구멍을 남기는 것이 맞는 동작입니다.
        """
        if self._count == 0:
            return None

        mean = self._total / self._count
        first_at = self._first_at
        self.reset()
        return mean, first_at

    def reset(self) -> None:
        self._count = 0
        self._total = 0.0
        self._first_at = None
