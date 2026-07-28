"""potner_llm 순수 로직 단위 테스트 (ROS / API 키 불필요)."""

from __future__ import annotations

from potner_llm.client import create_llm_client, describe_llm
from potner_llm.dialogue import DialogueService
from potner_llm.events import EventStore
from potner_llm.prompts import SYSTEM_PLANT, briefing_user_prompt
from potner_llm.status import MetricLevel, PlantStatus
from potner_llm.templates import render_briefing
from potner_llm.tools import ToolHub


def _sample_status(*, needs_attention: bool = False) -> PlantStatus:
    level = "low" if needs_attention else "normal"
    label = "건조" if needs_attention else "적정"
    metric = MetricLevel(name="soil", value=20.0 if needs_attention else 50.0, level=level, label_ko=label)
    ok = MetricLevel(name="ok", value=25.0, level="normal", label_ko="적정")
    return PlantStatus(
        timestamp="2026-07-28T00:00:00",
        soil=metric,
        temperature=ok,
        humidity=ok,
        light=ok,
        summary_ko="토양이 건조해요" if needs_attention else "전반적으로 양호해요",
        needs_attention=needs_attention,
    )


def test_system_prompt_is_korean_first_person():
    assert "식물" in SYSTEM_PLANT
    assert "1인칭" in SYSTEM_PLANT


def test_briefing_prompt_embeds_status():
    prompt = briefing_user_prompt({"요약": "양호"})
    assert "양호" in prompt
    assert "브리핑" in prompt


def test_template_briefing_fallback():
    text = render_briefing(_sample_status(needs_attention=False))
    assert "괜찮아" in text


def test_create_llm_client_mock_is_unavailable():
    client = create_llm_client({"llm": {"provider": "mock", "enabled": True}})
    assert client.provider == "mock"
    assert client.available() is False
    assert "mock" in describe_llm(client, {"llm": {"provider": "mock"}})


def test_tool_hub_get_status(tmp_path):
    store = EventStore(tmp_path / "events.jsonl")
    store.append({"message": "물 부족", "timestamp": "2026-07-28T00:00:00"})
    hub = ToolHub(get_status=_sample_status, event_store=store)
    status = hub.call("get_plant_status")
    assert status["주의필요"] is False
    events = hub.call("get_recent_events", {"limit": 1})
    assert events[0]["message"] == "물 부족"


def test_dialogue_offline_chat_fallback(tmp_path):
    store = EventStore(tmp_path / "events.jsonl")
    service = DialogueService(
        {"llm": {"provider": "mock"}},
        get_status=_sample_status,
        event_store=store,
    )
    reply = service.chat_once("지금 어때?")
    assert "로컬 모드" in reply
    assert "양호" in reply or "적정" in reply
