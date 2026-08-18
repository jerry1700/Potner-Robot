"""재생 큐 검증 — 중복 요청과 재생 충돌 제어.

음성 대화는 한 턴을 여러 조각으로 나눠 보냅니다. 그래서 순서·중복·선점·상한
네 가지가 전부 깨지기 쉽고, 깨지면 로봇이 같은 말을 두 번 하거나 이전
답변을 계속 읊습니다.
"""

import pytest

from potner_base.audio_queue import (
    MAX_PENDING,
    PlaybackDecision,
    PlaybackQueue,
    PlaybackRequest,
    dump_play_request,
    parse_play_request,
)


def chunk(turn_id="turn-1", seq=0, path=None, final=False):
    return PlaybackRequest(
        turn_id=turn_id,
        seq=seq,
        path=path or f"{turn_id}_{seq}.wav",
        final=final,
    )


# --- 봉투 파싱 ---


def test_정상_봉투를_읽는다():
    request = parse_play_request(
        '{"turnId":"abc","seq":3,"path":"abc_3.wav","final":true}'
    )
    assert request == PlaybackRequest(
        turn_id="abc", seq=3, path="abc_3.wav", final=True
    )


def test_final은_생략하면_거짓이다():
    assert parse_play_request('{"turnId":"a","seq":0,"path":"a.wav"}').final is False


def test_왕복_직렬화가_보존된다():
    original = chunk(turn_id="턴-한글", seq=7, final=True)
    assert parse_play_request(dump_play_request(original)) == original


@pytest.mark.parametrize(
    "payload,match",
    [
        ("not json at all", "JSON"),
        ("[1,2,3]", "객체"),
        ('{"seq":0,"path":"a.wav"}', "turnId"),
        ('{"turnId":"","seq":0,"path":"a.wav"}', "turnId"),
        ('{"turnId":"   ","seq":0,"path":"a.wav"}', "turnId"),
        # bool 은 int 의 하위형 — true 가 턴 "True" 로 통과하면 안 됩니다.
        ('{"turnId":true,"seq":0,"path":"a.wav"}', "turnId"),
        ('{"turnId":"a","path":"a.wav"}', "seq"),
        ('{"turnId":"a","seq":"0","path":"a.wav"}', "seq"),
        ('{"turnId":"a","seq":1.5,"path":"a.wav"}', "seq"),
        ('{"turnId":"a","seq":true,"path":"a.wav"}', "seq"),
        ('{"turnId":"a","seq":-1,"path":"a.wav"}', "0 이상"),
        ('{"turnId":"a","seq":0}', "path"),
        ('{"turnId":"a","seq":0,"path":"  "}', "path"),
        ('{"turnId":"a","seq":0,"path":"a.wav","final":"yes"}', "final"),
    ],
)
def test_어긋난_봉투를_거부한다(payload, match):
    """봉투가 깨졌다고 스피커 노드가 죽으면 안 되므로 ValueError 로 올립니다."""
    with pytest.raises(ValueError, match=match):
        parse_play_request(payload)


def test_숫자_턴아이디도_문자열로_받는다():
    assert parse_play_request('{"turnId":42,"seq":0,"path":"a.wav"}').turn_id == "42"


# --- 순서 ---


def test_같은_턴의_조각을_넣은_순서대로_꺼낸다():
    queue = PlaybackQueue()
    for seq in range(4):
        assert queue.submit_audio(chunk(seq=seq)) is PlaybackDecision.ACCEPTED

    assert [queue.pop(0).seq for _ in range(4)] == [0, 1, 2, 3]


def test_지각한_앞_조각을_버린다():
    """이미 3번을 재생했는데 1번이 도착하면 끼울 자리가 없습니다."""
    queue = PlaybackQueue()
    queue.submit_audio(chunk(seq=0))
    queue.submit_audio(chunk(seq=3))

    assert queue.submit_audio(chunk(seq=1)) is PlaybackDecision.STALE
    assert [job.seq for job in iter(lambda: queue.pop(0), None)] == [0, 3]


def test_seq가_띄어져도_받는다():
    """필러가 seq 를 차지하거나 TTS 조각이 실패해 번호가 빌 수 있습니다."""
    queue = PlaybackQueue()
    assert queue.submit_audio(chunk(seq=0)) is PlaybackDecision.ACCEPTED
    assert queue.submit_audio(chunk(seq=5)) is PlaybackDecision.ACCEPTED


# --- 중복 ---


def test_같은_조각을_두_번_받으면_한_번만_재생한다():
    queue = PlaybackQueue()
    assert queue.submit_audio(chunk(seq=0)) is PlaybackDecision.ACCEPTED
    assert queue.submit_audio(chunk(seq=0)) is PlaybackDecision.DUPLICATE

    assert queue.pending() == 1


def test_재생한_뒤에_온_재전송도_중복으로_막는다():
    """꺼내서 재생까지 끝난 조각이 다시 오면 같은 말을 두 번 합니다."""
    queue = PlaybackQueue()
    queue.submit_audio(chunk(seq=0))
    queue.pop(0)

    assert queue.submit_audio(chunk(seq=0)) is PlaybackDecision.DUPLICATE
    assert queue.pending() == 0


def test_마지막_조각의_재전송은_중복으로_보고한다():
    """final 이 턴을 은퇴시키므로, 검사 순서가 뒤바뀌면 STALE 로 잘못 잡힙니다."""
    queue = PlaybackQueue()
    queue.submit_audio(chunk(seq=0, final=True))

    assert queue.submit_audio(chunk(seq=0, final=True)) is PlaybackDecision.DUPLICATE


def test_중복_기억에는_상한이_있다():
    """무한히 쌓이면 오래 켜둔 로봇의 메모리가 계속 늘어납니다.

    상한을 넘긴 오래된 조각은 잊습니다. 그래도 seq 검사가 뒤를 받쳐주므로
    같은 턴의 지나간 조각이 되살아나지는 않습니다.
    """
    queue = PlaybackQueue(max_pending=1000, seen_limit=4)
    for seq in range(20):
        queue.submit_audio(chunk(seq=seq))

    assert len(queue._seen) == 4
    # 최근 4개는 아직 중복으로 막힙니다.
    assert queue.submit_audio(chunk(seq=19)) is PlaybackDecision.DUPLICATE
    # 잊은 오래된 조각은 seq 검사에서 걸립니다 (2차 방어선).
    assert queue.submit_audio(chunk(seq=0)) is PlaybackDecision.STALE


# --- 선점 (barge-in) ---


def test_새_턴이_이전_턴의_대기_조각을_버린다():
    queue = PlaybackQueue()
    for seq in range(3):
        queue.submit_audio(chunk(turn_id="old", seq=seq))

    assert queue.submit_audio(chunk(turn_id="new", seq=0)) is PlaybackDecision.PREEMPTED

    remaining = list(iter(lambda: queue.pop(0), None))
    assert [job.turn_id for job in remaining] == ["new"]
    assert queue.last_dropped == 3


def test_선점되면_재생_중인_조각이_무효가_된다():
    """대기열만 비워도 이미 재생 중인 조각은 계속 울립니다."""
    queue = PlaybackQueue()
    queue.submit_audio(chunk(turn_id="old", seq=0))
    playing = queue.pop(0)
    assert queue.is_current(playing)

    queue.submit_audio(chunk(turn_id="new", seq=0))

    assert not queue.is_current(playing)


def test_선점_뒤에_온_이전_턴_조각을_거부한다():
    """서버가 이전 턴을 아직 흘려보내고 있을 수 있습니다."""
    queue = PlaybackQueue()
    queue.submit_audio(chunk(turn_id="old", seq=0))
    queue.submit_audio(chunk(turn_id="new", seq=0))

    assert queue.submit_audio(chunk(turn_id="old", seq=1)) is PlaybackDecision.STALE


def test_첫_턴은_선점이_아니다():
    queue = PlaybackQueue()
    assert queue.submit_audio(chunk(turn_id="first", seq=0)) is PlaybackDecision.ACCEPTED


def test_끝난_턴의_지각_조각을_거부한다():
    queue = PlaybackQueue()
    queue.submit_audio(chunk(seq=0, final=True))

    assert queue.submit_audio(chunk(seq=1)) is PlaybackDecision.STALE


# --- 인사말(텍스트)과의 공존 ---


def test_인사말은_선점되지_않는다():
    """귀가 인사가 대화 때문에 잘리면 이상하고 다시 오지도 않습니다."""
    queue = PlaybackQueue()
    queue.submit_text("다녀오셨어요")
    greeting = queue.pop(0)

    queue.submit_audio(chunk(turn_id="turn-1", seq=0))

    assert queue.is_current(greeting)


def test_새_턴이_대기_중인_인사말을_버리지_않는다():
    queue = PlaybackQueue()
    queue.submit_audio(chunk(turn_id="old", seq=0))
    queue.submit_text("다녀오셨어요")

    queue.submit_audio(chunk(turn_id="new", seq=0))

    kinds = [job.kind for job in iter(lambda: queue.pop(0), None)]
    assert "text" in kinds


def test_인사말이_밀려도_오디오_조각을_버리지_않는다():
    """오디오를 버리면 문장이 중간부터 시작해 더 이상합니다."""
    queue = PlaybackQueue(max_pending=2)
    queue.submit_audio(chunk(seq=0))
    queue.submit_text("첫 인사")
    queue.submit_text("둘째 인사")

    jobs = list(iter(lambda: queue.pop(0), None))
    audio = [job for job in jobs if job.kind == "audio"]
    assert len(audio) == 1


# --- 상한 ---


def test_대기_조각이_상한을_넘으면_거부한다():
    """재생이 생성을 못 따라간 상태라 더 받아도 의미가 없습니다."""
    queue = PlaybackQueue(max_pending=3)
    for seq in range(3):
        assert queue.submit_audio(chunk(seq=seq)) is PlaybackDecision.ACCEPTED

    assert queue.submit_audio(chunk(seq=3)) is PlaybackDecision.OVERFLOW


def test_기본_상한은_평범한_대답을_담는다():
    """문장 단위로 쪼개면 보통 3~6조각입니다."""
    assert MAX_PENDING >= 6


# --- 종료·취소 ---


def test_전체_취소가_대기와_재생을_모두_무효화한다():
    queue = PlaybackQueue()
    queue.submit_audio(chunk(seq=0))
    queue.submit_audio(chunk(seq=1))
    playing = queue.pop(0)

    dropped = queue.cancel_all()

    assert dropped == 1
    assert not queue.is_current(playing)
    assert queue.pending() == 0


def test_취소_뒤에는_새_턴을_받는다():
    queue = PlaybackQueue()
    queue.submit_audio(chunk(turn_id="old", seq=0))
    queue.cancel_all()

    assert queue.submit_audio(chunk(turn_id="new", seq=0)) is PlaybackDecision.ACCEPTED


def test_빈_큐는_시간이_지나면_None을_돌려준다():
    assert PlaybackQueue().pop(0.01) is None
