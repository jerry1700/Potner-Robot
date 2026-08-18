"""재생 큐 — 중복 요청과 재생 충돌을 막는 판단 로직.

ROS 에 의존하지 않는 순수 파이썬 모듈입니다. speech.py 와 같은 이유입니다:
tests/ 는 rclpy 없이 도는 pytest 뿐이라, 판단이 노드 안에 들어가면 단위
테스트를 쓸 수 없습니다. 노드는 이 큐를 들고 재생만 합니다.

음성 대화는 한 번의 대화(=**턴**)를 오디오 **여러 조각**으로 나눠 보냅니다.
LLM 이 문장을 완성할 때마다 곧바로 TTS 를 돌려 흘려보내야 첫 소리가 빨리
나기 때문입니다. 그래서 이 큐가 지켜야 하는 것이 넷입니다.

  1. **순서** — 같은 턴의 조각은 seq 오름차순으로 재생해야 말이 됩니다.
     뒤늦게 도착한 앞 조각은 이미 지나간 자리에 끼울 수 없으니 버립니다.
  2. **중복** — 같은 (turnId, seq) 가 두 번 오면 두 번 말하면 안 됩니다.
     SSE 재연결이나 사용자의 재전송으로 실제로 두 번 옵니다.
  3. **선점** — 사용자가 새로 질문하면(=새 턴) 이전 답변은 즉시 그쳐야
     합니다. 큐에 남은 조각을 버리는 것만으로는 부족합니다. 이미 재생
     중인 조각도 끊어야 해서 generation 을 함께 올리고, 노드가
     is_current() 로 확인해 재생기 프로세스를 종료합니다.
  4. **상한** — 서버가 폭주해도 로봇이 몇 분 전 이야기를 계속 읊지 않게
     대기 조각 수를 제한합니다.

인사말(tts/say 로 들어오는 텍스트)은 **선점 대상이 아닙니다**. 귀가 인사가
대화 때문에 잘리면 이상하고, mission_manager 가 다시 보내주지도 않습니다.
그래서 텍스트 작업은 generation 검사를 통과시킵니다.

결정 종류를 열거형으로 두는 것은 potner_mission 의 command_session.py
(DecisionKind) 와 같은 관용구입니다.
"""

import json
import threading
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

# 한 턴에서 재생을 기다릴 수 있는 조각 수. 문장 단위로 쪼개니 평범한
# 대답은 3~6조각입니다. 이보다 밀리면 재생이 생성을 못 따라간 것이라
# 새 조각을 받아도 의미가 없습니다.
MAX_PENDING = 8

# 중복 판정용 기억 상한. 한 턴이 8조각이니 최근 열몇 턴을 덮습니다.
SEEN_LIMIT = 128

# 끝나거나 밀려난 턴 기억 상한. 지각 조각을 stale 로 거부하는 데 씁니다.
RETIRED_LIMIT = 64


class PlaybackDecision(Enum):
    """submit_audio() 의 판단 결과. 노드는 이걸 보고 로그만 남깁니다."""

    ACCEPTED = "accepted"
    """큐에 넣었습니다."""

    PREEMPTED = "preempted"
    """큐에 넣었고, 그 과정에서 이전 턴을 밀어냈습니다(새 질문이 들어옴)."""

    DUPLICATE = "duplicate"
    """같은 (turnId, seq) 를 이미 받았습니다. 조용히 무시합니다."""

    STALE = "stale"
    """이미 끝났거나 밀려난 턴의 조각, 또는 지나간 seq 입니다."""

    OVERFLOW = "overflow"
    """대기 조각이 상한을 넘었습니다."""


@dataclass(frozen=True)
class PlaybackRequest:
    """tts/play_audio 봉투 한 장."""

    turn_id: str
    seq: int
    path: str
    final: bool = False


@dataclass(frozen=True)
class PlaybackJob:
    """재생 스레드가 꺼내 쓰는 작업 한 건."""

    kind: str
    """``"text"``(합성해서 말하기) 또는 ``"audio"``(파일 재생)."""

    payload: str
    """말할 문장 또는 재생할 파일 경로."""

    generation: int = 0
    turn_id: Optional[str] = None
    seq: Optional[int] = None

    @property
    def preemptible(self) -> bool:
        """새 턴이 들어왔을 때 끊어도 되는 작업인지."""
        return self.kind == "audio"

    def label(self) -> str:
        """로그용 짧은 이름."""
        if self.kind == "audio":
            return f"{self.turn_id}#{self.seq}"
        return f"{self.payload[:20]}..."


def parse_play_request(payload: str) -> PlaybackRequest:
    """tts/play_audio 의 JSON 봉투를 검증해 요청으로 바꿉니다.

    봉투는 별도 프로세스(voice-chat-server)가 만든 것이라 형식을 믿을 수
    없습니다. 어긋나면 ValueError 를 올려서 노드가 로그만 남기고 계속
    돌게 합니다 — 봉투 하나가 깨졌다고 스피커가 죽으면 안 됩니다.

    Args:
        payload: ``{"turnId": "...", "seq": 0, "path": "...", "final": false}``

    Raises:
        ValueError: JSON 이 아니거나 필드가 어긋날 때
    """
    try:
        body = json.loads(payload)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"JSON 이 아닙니다: {exc}") from exc

    if not isinstance(body, dict):
        raise ValueError("봉투는 객체여야 합니다")

    turn_id = body.get("turnId")
    # bool 은 int 의 하위형이라 isinstance(True, int) 가 True 입니다.
    # {"turnId": true} 가 "True" 라는 턴으로 통과하면 안 됩니다.
    if isinstance(turn_id, bool) or not isinstance(turn_id, (str, int)):
        raise ValueError(f"turnId 가 잘못됐습니다: {turn_id!r}")
    turn_id = str(turn_id).strip()
    if not turn_id:
        raise ValueError("turnId 가 비어 있습니다")

    seq = body.get("seq")
    if isinstance(seq, bool) or not isinstance(seq, int):
        raise ValueError(f"seq 는 정수여야 합니다: {seq!r}")
    if seq < 0:
        raise ValueError(f"seq 는 0 이상이어야 합니다: {seq}")

    path = body.get("path")
    if not isinstance(path, str) or not path.strip():
        raise ValueError(f"path 가 잘못됐습니다: {path!r}")

    final = body.get("final", False)
    if not isinstance(final, bool):
        raise ValueError(f"final 은 불리언이어야 합니다: {final!r}")

    return PlaybackRequest(turn_id=turn_id, seq=seq, path=path.strip(), final=final)


def dump_play_request(request: PlaybackRequest) -> str:
    """요청을 봉투 JSON 으로 되돌립니다 (발행 측·테스트에서 씁니다).

    직렬화 옵션은 potner_bridge/command_result.py 와 통일합니다.
    """
    return json.dumps(
        {
            "turnId": request.turn_id,
            "seq": request.seq,
            "path": request.path,
            "final": request.final,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


@dataclass
class PlaybackQueue:
    """재생 대기열. 스레드 안전합니다.

    발행 스레드(ROS 콜백)가 submit_*, 재생 스레드가 pop/is_current 를
    부릅니다.
    """

    max_pending: int = MAX_PENDING
    seen_limit: int = SEEN_LIMIT
    retired_limit: int = RETIRED_LIMIT

    last_dropped: int = 0
    """직전 선점에서 버린 조각 수. 노드가 로그에 쓰는 값입니다."""

    _lock: threading.Condition = field(
        default_factory=lambda: threading.Condition(threading.Lock()), repr=False
    )
    _jobs: deque = field(default_factory=deque, repr=False)
    _generation: int = 0
    _turn_id: Optional[str] = None
    _max_seq: int = -1
    _seen: "OrderedDict[tuple, None]" = field(default_factory=OrderedDict, repr=False)
    _retired: "OrderedDict[str, None]" = field(default_factory=OrderedDict, repr=False)

    # --- 투입 ---

    def submit_text(self, text: str) -> PlaybackDecision:
        """합성해서 말할 문장을 넣습니다 (인사말 등).

        중복·선점 대상이 아닙니다. 상한만 봅니다.
        """
        with self._lock:
            if len(self._jobs) >= self.max_pending:
                # 오래된 텍스트를 버립니다 — 기존 speaker 동작 유지.
                # 오디오를 버리면 문장이 중간부터 시작하므로 건드리지
                # 않습니다. 오디오 수는 submit_audio 가 따로 제한합니다.
                self._drop_oldest_text()
            self._jobs.append(PlaybackJob(kind="text", payload=text))
            self._lock.notify()
            return PlaybackDecision.ACCEPTED

    def submit_audio(self, request: PlaybackRequest) -> PlaybackDecision:
        """음성 대화 오디오 조각을 넣습니다."""
        with self._lock:
            key = (request.turn_id, request.seq)

            # 1. 중복 — retired 검사보다 먼저 봐야 합니다. 턴의 마지막
            #    조각이 재전송되면 그 턴은 이미 retired 라서, 순서가
            #    바뀌면 DUPLICATE 대신 STALE 로 잘못 보고합니다.
            if key in self._seen:
                self._seen.move_to_end(key)
                return PlaybackDecision.DUPLICATE

            # 2. 이미 끝났거나 밀려난 턴의 새 조각
            if request.turn_id in self._retired:
                return PlaybackDecision.STALE

            preempted = False
            if request.turn_id != self._turn_id:
                # 3. 새 턴 — 이전 답변을 끊습니다(barge-in).
                if self._turn_id is not None:
                    self._retire(self._turn_id)
                    self.last_dropped = self._drop_audio_jobs()
                    self._generation += 1
                    preempted = True
                self._turn_id = request.turn_id
                self._max_seq = -1

            # 4. 지각한 앞 조각 — 이미 지나간 자리에 끼울 수 없습니다.
            if request.seq <= self._max_seq:
                return PlaybackDecision.STALE

            # 5. 상한 — 오디오는 오래된 것을 버리지 않습니다. 앞 조각을
            #    버리면 말이 중간부터 시작해 더 이상합니다.
            if self._pending_audio() >= self.max_pending:
                return PlaybackDecision.OVERFLOW

            self._remember(key)
            self._max_seq = request.seq
            self._jobs.append(
                PlaybackJob(
                    kind="audio",
                    payload=request.path,
                    generation=self._generation,
                    turn_id=request.turn_id,
                    seq=request.seq,
                )
            )
            if request.final:
                # 마지막 조각까지 받았으니 지각 조각은 이제 전부 stale.
                self._retire(request.turn_id)

            self._lock.notify()
            return PlaybackDecision.PREEMPTED if preempted else PlaybackDecision.ACCEPTED

    # --- 재생 ---

    def pop(self, timeout: float = 0.2) -> Optional[PlaybackJob]:
        """다음 작업을 꺼냅니다. 없으면 timeout 뒤에 None."""
        with self._lock:
            if not self._jobs:
                self._lock.wait(timeout)
            if not self._jobs:
                return None
            return self._jobs.popleft()

    def is_current(self, job: PlaybackJob) -> bool:
        """재생 중인 작업이 아직 유효한지. False 면 즉시 끊어야 합니다."""
        if not job.preemptible:
            return True
        with self._lock:
            return job.generation == self._generation

    def cancel_all(self) -> int:
        """대기 중인 오디오를 전부 버리고 재생 중인 것도 무효화합니다."""
        with self._lock:
            dropped = self._drop_audio_jobs()
            if self._turn_id is not None:
                self._retire(self._turn_id)
                self._turn_id = None
            self._generation += 1
            return dropped

    def wake(self) -> None:
        """종료 시 pop() 대기를 즉시 깨웁니다."""
        with self._lock:
            self._lock.notify_all()

    # --- 들여다보기 (테스트·로그용) ---

    def pending(self) -> int:
        with self._lock:
            return len(self._jobs)

    def current_turn(self) -> Optional[str]:
        with self._lock:
            return self._turn_id

    def generation(self) -> int:
        with self._lock:
            return self._generation

    # --- 내부 ---

    def _pending_audio(self) -> int:
        return sum(1 for job in self._jobs if job.kind == "audio")

    def _drop_audio_jobs(self) -> int:
        """대기열에서 오디오만 걷어냅니다. 텍스트(인사말)는 남깁니다."""
        kept = deque(job for job in self._jobs if job.kind != "audio")
        dropped = len(self._jobs) - len(kept)
        self._jobs = kept
        return dropped

    def _drop_oldest_text(self) -> None:
        for index, job in enumerate(self._jobs):
            if job.kind == "text":
                del self._jobs[index]
                return

    def _remember(self, key) -> None:
        self._seen[key] = None
        while len(self._seen) > self.seen_limit:
            self._seen.popitem(last=False)

    def _retire(self, turn_id: str) -> None:
        self._retired[turn_id] = None
        self._retired.move_to_end(turn_id)
        while len(self._retired) > self.retired_limit:
            self._retired.popitem(last=False)
