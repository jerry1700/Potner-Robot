"""귀가 반김(마중) 판단.

ROS 에 의존하지 않는 순수 모듈입니다. 시각을 float 초로 받아 CI 에서
시계 없이 검증합니다.

왜 "무장(arm)" 개념이 필요한가 — YOLO 는 "사람"을 인식하지 "그 사용자"를
인식하지 못합니다. 사람이 보일 때마다 인사하면 집에 있던 가족이 지나가도
인사합니다. 그래서 앱의 귀가 알림(지오펜스)이 인사를 무장시키고, 카메라가
무장된 동안에만 발사합니다.

    귀가 알림 ──> arm ──┐
                        ├─ 시간창 안에 사람 인식 ──> 인사 (한 번) ──> 해제
                        └─ 시간 초과 ──────────────> 조용히 해제

GPS 가 부정확해도(아파트에서 흔함) 최악의 결과가 "인사를 안 함"이지
"아무에게나 인사함"이 아니게 됩니다.
"""

from dataclasses import dataclass, field


@dataclass
class GreetingPolicy:
    """무장 시간창과 재인사 억제를 함께 판단합니다.

    Args:
        arm_duration: 무장 유지 시간 (s). 귀가 알림 후 이 시간 안에
            사람이 보여야 인사합니다
        cooldown: 인사 후 재인사 금지 시간 (s). 같은 사람이 카메라 앞을
            서성일 때 연달아 인사하는 것을 막습니다
    """

    arm_duration: float = 600.0
    cooldown: float = 300.0
    _armed_at: float = field(default=None, repr=False)
    _armed_duration: float = field(default=None, repr=False)
    _last_greeting: float = field(default=None, repr=False)

    def arm(self, now: float, duration: float = None) -> None:
        """귀가 알림을 받고 서버가 준 대기시간으로 이번 시간창을 엽니다."""
        if duration is not None and duration <= 0:
            raise ValueError("인사 대기시간은 양수여야 합니다.")
        self._armed_at = now
        self._armed_duration = (
            self.arm_duration if duration is None else float(duration)
        )

    def disarm(self) -> None:
        self._armed_at = None
        self._armed_duration = None

    def is_armed(self, now: float) -> bool:
        if self._armed_at is None:
            return False
        if now - self._armed_at >= self._armed_duration:
            # 시간창이 지났으면 상태도 정리합니다. 며칠 뒤의 now 가 다시
            # 들어와도 낡은 _armed_at 이 남아 있지 않게.
            self._armed_at = None
            self._armed_duration = None
            return False
        return True

    def should_greet(self, now: float, person_present: bool) -> bool:
        """지금 인사해야 하는지. True 를 돌려줘도 상태는 바꾸지 않습니다.

        실제로 인사를 시작했을 때만 mark_greeted() 를 부르세요. 판단과
        기록을 나눈 이유는, 노드가 GREETING 전이에 실패했을 때(다른 임무
        진입 등) 인사 기회가 소모되지 않아야 하기 때문입니다.
        """
        if not person_present:
            return False
        if not self.is_armed(now):
            return False
        if self._last_greeting is not None:
            if now - self._last_greeting < self.cooldown:
                return False
        return True

    def mark_greeted(self, now: float) -> None:
        """인사를 시작했습니다. 시간창을 닫고 쿨다운을 시작합니다.

        무장 한 번에 인사 한 번입니다. 닫지 않으면 사용자가 거실을 오갈
        때마다 시간창이 끝날 때까지 계속 인사합니다.
        """
        self._last_greeting = now
        self._armed_at = None
        self._armed_duration = None
