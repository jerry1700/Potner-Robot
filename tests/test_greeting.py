"""귀가 반김(마중) 판단 검증.

핵심은 "무장되지 않았으면 인사하지 않는다"입니다. 이게 깨지면 집에 있던
가족이 카메라 앞을 지나갈 때마다 로봇이 인사합니다.
"""

from potner_mission.greeting import GreetingPolicy


def policy(**kwargs):
    return GreetingPolicy(arm_duration=600.0, cooldown=300.0, **kwargs)


def test_무장_전에는_사람이_보여도_인사하지_않는다():
    """YOLO 는 '사람'을 인식하지 '그 사용자'를 인식하지 못합니다."""
    p = policy()
    assert p.should_greet(100.0, person_present=True) is False


def test_무장_중_사람이_보이면_인사한다():
    p = policy()
    p.arm(100.0)
    assert p.should_greet(150.0, person_present=True) is True


def test_사람이_없으면_무장_중이어도_인사하지_않는다():
    p = policy()
    p.arm(100.0)
    assert p.should_greet(150.0, person_present=False) is False


def test_시간창이_지나면_해제된다():
    """귀가 알림이 왔는데 사용자가 한참 안 들어온 경우. 며칠 뒤에 들어온
    사람에게 그때의 알림으로 인사하면 안 됩니다."""
    p = policy()
    p.arm(100.0)

    assert p.should_greet(100.0 + 599.9, person_present=True) is True
    assert p.should_greet(100.0 + 600.0, person_present=True) is False


def test_인사는_무장_한_번에_한_번이다():
    """닫지 않으면 사용자가 거실을 오갈 때마다 시간창이 끝날 때까지
    계속 인사합니다."""
    p = policy()
    p.arm(100.0)
    p.mark_greeted(150.0)

    assert p.should_greet(160.0, person_present=True) is False


def test_판단은_상태를_바꾸지_않는다():
    """should_greet 가 True 를 돌려줬다고 인사 기회가 소모되면 안 됩니다.
    노드가 상태 전이에 실패했을 때 재시도할 수 있어야 합니다."""
    p = policy()
    p.arm(100.0)

    assert p.should_greet(150.0, person_present=True) is True
    assert p.should_greet(151.0, person_present=True) is True  # 아직 유효


def test_쿨다운_안에는_다시_무장해도_인사하지_않는다():
    """앱이 지오펜스를 들락거리며 알림을 연발해도 인사는 한 번입니다."""
    p = policy()
    p.arm(100.0)
    p.mark_greeted(150.0)

    p.arm(200.0)  # 재무장
    assert p.should_greet(250.0, person_present=True) is False  # 쿨다운 중
    assert p.should_greet(150.0 + 300.0, person_present=True) is True


def test_재무장하면_시간창이_연장된다():
    p = policy()
    p.arm(100.0)
    p.arm(500.0)

    assert p.should_greet(500.0 + 599.0, person_present=True) is True


def test_서버가_준_대기시간을_이번_귀가에_적용한다():
    p = policy()
    p.arm(100.0, duration=120.0)

    assert p.should_greet(219.9, person_present=True) is True
    assert p.should_greet(220.0, person_present=True) is False


def test_수동_해제():
    p = policy()
    p.arm(100.0)
    p.disarm()

    assert p.is_armed(101.0) is False


def test_시간창_만료_후_무장_상태가_남지_않는다():
    """만료된 뒤 is_armed 를 물으면 내부 상태도 정리되어야 합니다.
    mission_manager 가 이 값으로 새 임무 시작 여부를 정하기 때문입니다."""
    p = policy()
    p.arm(100.0)

    assert p.is_armed(100.0 + 700.0) is False
    assert p.is_armed(100.0 + 50.0) is False  # 과거 시각이 와도 무장 아님
