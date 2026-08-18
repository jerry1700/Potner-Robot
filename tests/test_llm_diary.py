"""일기 생성 — 프롬프트 구조·페르소나·사실성 제약·폴백 테스트 (네트워크 없음)."""

from __future__ import annotations

from potner_llm.dialogue import DialogueService
from potner_llm.events import EventStore
from potner_llm.prompts import diary_system_prompt, diary_user_prompt
from potner_llm.status import MetricLevel, PlantStatus
from potner_llm.templates import render_diary


def _status(*, needs_attention: bool = False, temp: float = 25.0) -> PlantStatus:
    ok = MetricLevel(name="ok", value=50.0, level="normal", label_ko="적정")
    soil = (
        MetricLevel(name="soil", value=18.0, level="low", label_ko="건조")
        if needs_attention
        else ok
    )
    return PlantStatus(
        timestamp="2026-07-31T21:00:00",
        soil=soil,
        temperature=MetricLevel(name="temperature", value=temp, level="normal", label_ko="적정"),
        humidity=ok,
        light=ok,
        summary_ko="토양이 건조해서 물이 필요해요" if needs_attention else "전반적으로 양호해요",
        needs_attention=needs_attention,
    )


# --- 시스템 프롬프트: 페르소나·말투·형식·제약 ---


def test_diary_system_prompt_has_default_persona_and_constraints():
    prompt = diary_system_prompt()
    assert "초록이" in prompt and "1인칭" in prompt
    assert "일기" in prompt
    # 출력 형식이 정의돼 있다
    assert "3~6문장" in prompt and "300자" in prompt
    # 허위 정보 방지 제약이 명시돼 있다
    assert "지어내지 않는다" in prompt
    assert "안전/의료 조언" in prompt


def test_diary_system_prompt_reflects_custom_persona_and_speech_style():
    prompt = diary_system_prompt({"이름": "토토", "종": "몬스테라", "말투": "씩씩한 존댓말"})
    assert "토토" in prompt and "몬스테라" in prompt and "씩씩한 존댓말" in prompt
    assert "초록이" not in prompt


# --- 사용자 프롬프트: 입력 데이터 반영 구조 ---


def test_diary_user_prompt_embeds_date_status_events_activities():
    prompt = diary_user_prompt(
        {"요약": "전반적으로 양호해요"},
        [{"message": "낮에 조도가 낮았어요"}],
        date="2026-07-31",
        user_activities=["아침에 인사해줬다"],
    )
    assert "2026-07-31" in prompt
    assert "전반적으로 양호해요" in prompt
    assert "낮에 조도가 낮았어요" in prompt
    assert "아침에 인사해줬다" in prompt
    assert "이 기록만 근거로" in prompt


def test_diary_user_prompt_marks_missing_records():
    prompt = diary_user_prompt({"요약": "양호"}, [])
    assert "(기록 없음)" in prompt


# --- 템플릿 폴백 (LLM 없이) ---


def test_render_diary_quiet_healthy_day():
    text = render_diary(_status(), [], date="2026-07-31")
    assert text.startswith("2026-07-31의 일기.")
    assert "조용한 하루" in text
    assert "양호" in text


def test_render_diary_with_events_and_attention():
    events = [{"message": "물이 부족했어요"}, {"message": "조도가 낮았어요"}]
    text = render_diary(_status(needs_attention=True), events)
    assert "물이 부족했어요" in text and "조도가 낮았어요" in text
    assert "신경 쓰여" in text


# --- DialogueService.diary: 생성·검증·폴백 흐름 ---


def _diary_service(tmp_path, reply=None, **kwargs):
    store = EventStore(tmp_path / "events.jsonl")
    store.append({"message": "낮에 잠깐 건조했어요", "timestamp": "2026-07-31T14:00:00"})
    status = kwargs.pop("status", None) or _status()
    service = DialogueService(
        {"llm": {"provider": "gms" if reply is not None else "mock"}},
        get_status=lambda: status,
        event_store=store,
        **kwargs,
    )
    captured = {}
    if reply is not None:
        service.llm.api_key = "fake-key"

        def fake_complete(user_prompt, *, system, temperature=0.7):
            captured["user_prompt"] = user_prompt
            captured["system"] = system
            return reply

        service.llm.complete = fake_complete
    return service, captured


def test_diary_offline_falls_back_to_template(tmp_path):
    service, _ = _diary_service(tmp_path)  # mock provider → LLM 사용 불가
    text = service.diary(date="2026-07-31")
    assert "2026-07-31의 일기." in text
    assert "낮에 잠깐 건조했어요" in text  # 이벤트가 폴백에도 반영된다


def test_diary_returns_llm_text_and_passes_grounding_data(tmp_path):
    reply = "오늘은 볕이 좋아서 기분 좋은 하루였어. 내일도 이랬으면 좋겠다."
    service, captured = _diary_service(tmp_path, reply=reply)
    text = service.diary(date="2026-07-31", persona={"이름": "토토"})
    assert text == reply
    # 근거 데이터와 페르소나가 프롬프트에 실렸는지
    assert "낮에 잠깐 건조했어요" in captured["user_prompt"]
    assert "2026-07-31" in captured["user_prompt"]
    assert "토토" in captured["system"]
    assert "지어내지 않는다" in captured["system"]


def test_diary_contradicting_sensor_claim_falls_back(tmp_path):
    # 상태는 25도인데 34도였다고 쓴 일기 → 사실성 검증 탈락 → 템플릿 폴백
    reply = "오늘은 34도까지 올라가서 정말 힘들었어."
    service, _ = _diary_service(tmp_path, reply=reply)
    text = service.diary()
    assert "34도" not in text
    assert "낮에 잠깐 건조했어요" in text  # 기록 기반 폴백


def test_diary_scenarios_flow_into_prompt(tmp_path):
    # 물 부족 상황이 입력 데이터에 그대로 실리는지 (상황별 생성의 근거 확인)
    reply = "오늘은 목이 말랐어. 내일은 물을 마시고 싶다."
    service, captured = _diary_service(
        tmp_path, reply=reply, status=_status(needs_attention=True)
    )
    text = service.diary()
    assert text == reply
    assert "건조" in captured["user_prompt"]
    assert "물이 필요해요" in captured["user_prompt"]
