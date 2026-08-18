"""factcheck.verify_response — LLM 응답과 센서·프로필 데이터의 사실성 대조 테스트.

네트워크·LLM 호출 없이 순수 로직만 검증한다.
"""

from __future__ import annotations

import logging


from potner_llm.dialogue import DialogueService
from potner_llm.events import EventStore
from potner_llm.factcheck import (
    ACTION_FALLBACK,
    ACTION_PASS,
    ACTION_RETRY,
    FactCheckPolicy,
    FactCheckResult,
    next_action,
    snapshot_from_status,
    verify_response,
)
from potner_llm.models import PlantProfile, SensorSnapshot
from potner_llm.status import MetricLevel, PlantStatus

SENSORS = SensorSnapshot(soil=42.0, temp=26.1, humidity=55.0, light=800.0, co2=650.0)


# --- 정상 시나리오: 데이터와 일치하는 답변은 통과 ---


def test_accepts_answer_matching_sensor_values():
    result = verify_response("지금 흙 수분이 42%라서 딱 좋아. 온도는 26도야.", SENSORS)
    assert result.ok is True
    assert result.issues == ()
    assert result.claims_checked >= 2


def test_accepts_rounded_values_within_tolerance():
    # LLM이 반올림해 말하는 것은 정상 — 26.1°C를 "26도", 42%를 "40% 정도"로
    result = verify_response("한 40% 정도로 촉촉하고 26도쯤 돼.", SENSORS)
    assert result.ok is True


def test_accepts_answer_without_numeric_claims():
    result = verify_response("오늘도 기분 좋게 자라고 있어!", SENSORS)
    assert result.ok is True
    assert result.claims_checked == 0


def test_accepts_unitless_numbers():
    # "3문장", "이틀" 같은 단위 없는 숫자는 센서와 무관 — 검사하지 않는다
    result = verify_response("이틀에 1번이면 충분해. 3번은 너무 많아.", SENSORS)
    assert result.ok is True
    assert result.claims_checked == 0


def test_accepts_empty_text():
    # 빈 응답은 check_response(validator)가 잡는다 — 여기서는 대조할 게 없음
    assert verify_response("", SENSORS).ok is True
    assert verify_response(None, SENSORS).ok is True


# --- 허위 응답: 센서와 상충되는 수치는 통과하지 못한다 ---


def test_rejects_soil_value_contradicting_sensor():
    result = verify_response("흙 수분이 80%나 돼서 물은 필요 없어.", SENSORS)
    assert result.ok is False
    assert "sensor_mismatch:soil" in result.issue_codes


def test_rejects_temperature_contradicting_sensor():
    result = verify_response("지금 34도라서 너무 더워.", SENSORS)
    assert result.ok is False
    assert "sensor_mismatch:temp" in result.issue_codes


def test_accepts_negative_temperature_matching_sensor():
    # 실측이 영하일 때 정답("영하 5도"/"-5도")이 +5로 파싱돼 기각되던 오탐 회귀 방지
    cold = SensorSnapshot(temp=-5.0)
    assert verify_response("지금 영하 5도라서 너무 추워.", cold).ok is True
    assert verify_response("지금 -5도야, 얼겠어.", cold).ok is True


def test_rejects_fabricated_negative_temperature():
    result = verify_response("지금 영하 5도야.", SENSORS)  # 실측 26.1°C
    assert result.ok is False
    assert "sensor_mismatch:temp" in result.issue_codes


def test_range_expression_not_misread_as_negative():
    # "50-60%"의 '-'는 범위 표기다 — -60%로 오독하면 정상 답변이 기각된다
    result = verify_response("토양 수분은 50-60% 사이야.", SensorSnapshot(soil=55.0))
    assert result.ok is True


def test_rejects_light_far_from_sensor():
    result = verify_response("조도가 5000 lux나 돼서 눈부셔.", SENSORS)
    assert result.ok is False
    assert "sensor_mismatch:light" in result.issue_codes


def test_rejects_co2_far_from_sensor():
    result = verify_response("CO2가 2000ppm이라 답답해.", SENSORS)
    assert result.ok is False
    assert "sensor_mismatch:co2" in result.issue_codes


def test_rejects_humidity_contradicting_sensor():
    result = verify_response("습도가 20%밖에 안 돼서 건조해.", SENSORS)
    assert result.ok is False
    assert "sensor_mismatch:humidity" in result.issue_codes


def test_ambiguous_percent_passes_if_it_matches_any_metric():
    # 문맥 없는 %는 토양수분/습도 중 하나와만 맞아도 억지로 틀렸다고 하지 않는다
    result = verify_response("55%야.", SENSORS)  # humidity=55와 일치
    assert result.ok is True


def test_ambiguous_percent_fails_if_it_matches_nothing():
    result = verify_response("90%쯤 되는 것 같아.", SENSORS)
    assert result.ok is False


# --- 데이터 누락: 없는 값을 말하면 날조로 잡는다 ---


def test_rejects_numeric_claim_when_sensor_missing():
    empty = SensorSnapshot()
    result = verify_response("지금 온도가 24도야.", empty)
    assert result.ok is False
    assert "fabricated_value:temp" in result.issue_codes


def test_rejects_soil_percent_claim_when_soil_missing():
    no_soil = SensorSnapshot(temp=24.0)
    result = verify_response("흙 수분이 50%야.", no_soil)
    assert result.ok is False
    assert "fabricated_value:soil" in result.issue_codes


def test_missing_sensors_pass_when_answer_makes_no_claims():
    result = verify_response("잘 모르겠지만 기분은 좋아!", SensorSnapshot())
    assert result.ok is True


def test_state_claims_are_not_checked_without_soil_data():
    # 값이 없으면 감상 표현("촉촉해")까지 막지 않는다 — 수치 날조만 잡는다
    result = verify_response("촉촉하고 편안한 하루야.", SensorSnapshot())
    assert result.ok is True


# --- 센서 이상(극단값)과 상태 표현의 상충 ---


def test_rejects_wet_claim_when_soil_is_very_dry():
    dry = SensorSnapshot(soil=12.0)
    result = verify_response("흙이 아주 촉촉해서 물은 충분해!", dry)
    assert result.ok is False
    assert "state_conflict:soil" in result.issue_codes


def test_rejects_dry_claim_when_soil_is_very_wet():
    wet = SensorSnapshot(soil=85.0)
    result = verify_response("너무 목말라. 물이 필요해.", wet)
    assert result.ok is False
    assert "state_conflict:soil" in result.issue_codes


def test_accepts_state_claim_matching_extreme_sensor():
    dry = SensorSnapshot(soil=12.0)
    result = verify_response("흙이 바싹 말라서 목말라.", dry)
    assert result.ok is True


# --- 지어낸 이벤트: 급수 기록이 없는데 급수를 회상 ---


def test_rejects_watering_memory_without_record():
    profile = PlantProfile(last_watered_at=None)
    result = verify_response("아까 물 줬잖아, 기억 안 나?", SENSORS, profile)
    assert result.ok is False
    assert "fabricated_event:watering" in result.issue_codes


def test_accepts_watering_memory_with_record():
    profile = PlantProfile(last_watered_at="2026-07-30T09:00")
    result = verify_response("어제 물을 줬으니까 오늘은 괜찮아.", SENSORS, profile)
    assert result.ok is True


def test_watering_check_skipped_without_profile():
    result = verify_response("아까 물 줬잖아!", SENSORS)
    assert result.ok is True


# --- 허용 오차 정책 조정 ---


def test_policy_tolerance_is_configurable():
    strict = FactCheckPolicy(temp_tolerance=0.1)
    result = verify_response("지금 27도야.", SENSORS, policy=strict)
    assert result.ok is False

    loose = FactCheckPolicy(temp_tolerance=5.0)
    result = verify_response("지금 27도야.", SENSORS, policy=loose)
    assert result.ok is True


# --- 재시도/폴백 판정 ---


def test_next_action_pass_when_ok():
    assert next_action(FactCheckResult(ok=True), attempt=0) == ACTION_PASS


def test_next_action_retry_then_fallback():
    failed = verify_response("지금 34도라서 더워.", SENSORS)
    assert failed.ok is False
    assert next_action(failed, attempt=0) == ACTION_RETRY
    assert next_action(failed, attempt=1) == ACTION_FALLBACK


def test_next_action_respects_policy_max_retries():
    failed = FactCheckResult(ok=False)
    policy = FactCheckPolicy(max_retries=0)
    assert next_action(failed, attempt=0, policy=policy) == ACTION_FALLBACK


# --- 로깅 ---


def test_failure_is_logged_with_reasons(caplog):
    with caplog.at_level(logging.WARNING, logger="potner_llm.factcheck"):
        verify_response("지금 34도라서 더워.", SENSORS)
    assert any("사실성 검증 실패" in rec.getMessage() for rec in caplog.records)
    assert any("sensor_mismatch:temp" in rec.getMessage() for rec in caplog.records)


def test_success_is_logged(caplog):
    with caplog.at_level(logging.INFO, logger="potner_llm.factcheck"):
        verify_response("지금 26도야.", SENSORS)
    assert any("사실성 검증 통과" in rec.getMessage() for rec in caplog.records)


# --- 복합 시나리오: 여러 위반이 모두 사유로 남는다 ---


def test_multiple_issues_are_all_reported():
    profile = PlantProfile(last_watered_at=None)
    result = verify_response(
        "아까 물 줬잖아. 지금 34도고 흙 수분은 80%야.", SENSORS, profile
    )
    assert result.ok is False
    codes = result.issue_codes
    assert "fabricated_event:watering" in codes
    assert "sensor_mismatch:temp" in codes
    assert "sensor_mismatch:soil" in codes


# --- DialogueService(chat_once) 배선: 음성/CLI 경로에서도 검증이 동작한다 ---


def _plant_status(temp: float = 25.0) -> PlantStatus:
    ok = MetricLevel(name="ok", value=50.0, level="normal", label_ko="적정")
    return PlantStatus(
        timestamp="2026-07-31T00:00:00",
        soil=ok,
        temperature=MetricLevel(name="temperature", value=temp, level="normal", label_ko="적정"),
        humidity=ok,
        light=MetricLevel(name="light", value=800.0, level="normal", label_ko="적정"),
        summary_ko="전반적으로 양호해요",
        needs_attention=False,
    )


def test_snapshot_from_status_maps_metric_values():
    snapshot = snapshot_from_status(_plant_status(temp=26.0))
    assert snapshot.temp == 26.0
    assert snapshot.soil == 50.0
    assert snapshot.light == 800.0


def _dialogue_with_replies(tmp_path, replies: list, **kwargs) -> tuple:
    """가짜 chat_with_tools가 replies를 순서대로 돌려주는 DialogueService."""
    store = EventStore(tmp_path / "events.jsonl")
    service = DialogueService(
        {"llm": {"provider": "gms"}},
        get_status=_plant_status,
        event_store=store,
        **kwargs,
    )
    service.llm.api_key = "fake-key"
    calls = {"count": 0}

    def fake_chat_with_tools(messages, tools, system=None):
        index = min(calls["count"], len(replies) - 1)
        calls["count"] += 1
        return replies[index]

    service.llm.chat_with_tools = fake_chat_with_tools
    return service, calls


def test_chat_once_replaces_contradicting_reply_with_grounded_fallback(tmp_path):
    # 상태는 25도인데 계속 34도라고 주장 → 재생성 1회 후에도 실패 → 상태 기반 폴백
    service, calls = _dialogue_with_replies(tmp_path, ["지금 34도라서 너무 더워."])
    reply = service.chat_once("더워?")
    assert calls["count"] == 2  # 최초 1회 + 재생성 1회
    assert "34도" not in reply
    assert "전반적으로 양호해요" in reply
    # 폴백도 히스토리에 남아 다음 턴 맥락이 된다
    assert service.history[-1].content == reply


def test_chat_once_retry_then_delivers_corrected_reply(tmp_path):
    service, calls = _dialogue_with_replies(
        tmp_path, ["지금 34도야.", "지금 25도라서 딱 좋아."]
    )
    reply = service.chat_once("몇 도야?")
    assert calls["count"] == 2
    assert reply == "지금 25도라서 딱 좋아."


def test_chat_once_verify_facts_off_keeps_raw_reply(tmp_path):
    service, calls = _dialogue_with_replies(
        tmp_path, ["지금 34도라서 너무 더워."], verify_facts=False
    )
    reply = service.chat_once("더워?")
    assert calls["count"] == 1
    assert reply == "지금 34도라서 너무 더워."
