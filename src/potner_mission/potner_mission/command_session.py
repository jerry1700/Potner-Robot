"""서버 명령 세션이 공통으로 쓰는 판정 결과 타입.

귀가 마중과 이동이 같은 네 가지 판정을 합니다. 로봇이 하나뿐이라
"지금 이 명령을 받아도 되는가" 를 묻는 자리가 같기 때문입니다.
"""

from dataclasses import dataclass
from enum import Enum, auto
from typing import Optional


class DecisionKind(Enum):
    """받은 명령을 어떻게 할지."""

    #: 수행한다.
    ACCEPTED = auto()

    #: 지금 수행 중인 바로 그 명령이 다시 왔다. **회신하지 않는다.**
    #:
    #: QoS 1 재전송이다. 여기서 BUSY 를 보내면 서버가 수행 중인 명령을
    #: 실패로 확정해 (BUSY 도 종단 상태다) 자동 케어 체인이 끊기고, 진짜
    #: 도착해서 보낸 OK 는 ALREADY_COMPLETED 로 버려진다.
    DUPLICATE_PENDING = auto()

    #: 이미 끝낸 명령이 다시 왔다. 그때 보낸 결과를 다시 보낸다.
    REPLAY = auto()

    #: **다른** 명령이 수행 중이라 받을 수 없다. BUSY 는 이때만 쓴다.
    BUSY = auto()


@dataclass(frozen=True)
class MissionResult:
    command_name: str
    request_id: str
    status: str
    error: Optional[str] = None
    code: Optional[str] = None


@dataclass(frozen=True)
class CommandDecision:
    kind: DecisionKind
    result: Optional[MissionResult] = None
