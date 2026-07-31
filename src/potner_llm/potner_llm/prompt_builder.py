"""식물 프로필·센서 상태를 반영한 동적 시스템 프롬프트 생성."""

from __future__ import annotations

from .config import ServiceSettings
from .models import PlantProfile, SensorSnapshot
from .sensor_provider import attention_messages, classify_metric


def build_system_prompt(
    profile: PlantProfile,
    sensors: SensorSnapshot,
    settings: ServiceSettings,
) -> str:
    """식물 정보와 현재 센서 상태를 포함한 시스템 프롬프트를 만든다.

    사용자 질문은 시스템 프롬프트가 아니라 user 메시지로 따로 전달한다 —
    질문을 시스템 프롬프트에 섞으면 프롬프트 주입에 더 취약해진다.

    원시값에 판정 라벨(건조/적정/과습 등)을 붙여 준다 — "42%"만 주면
    LLM이 그게 마른 건지 젖은 건지 스스로 지어내기 때문이다.
    """
    sensor_lines = []
    if sensors.soil is not None:
        label = classify_metric("soil", sensors.soil).label_ko
        sensor_lines.append(f"- 토양 수분: {sensors.soil:g}% ({label})")
    if sensors.temp is not None:
        label = classify_metric("temperature", sensors.temp).label_ko
        sensor_lines.append(f"- 온도: {sensors.temp:g}°C ({label})")
    if sensors.humidity is not None:
        label = classify_metric("humidity", sensors.humidity).label_ko
        sensor_lines.append(f"- 습도: {sensors.humidity:g}% ({label})")
    if sensors.light is not None:
        label = classify_metric("light", sensors.light).label_ko
        sensor_lines.append(f"- 조도: {sensors.light:g} lux ({label})")
    if sensors.co2 is not None:
        sensor_lines.append(f"- CO₂: {sensors.co2:g} ppm")
    if sensors.photo_summary:
        sensor_lines.append(f"- 최근 촬영 결과: {sensors.photo_summary}")
    issues = attention_messages(sensors)
    if issues:
        sensor_lines.append(f"- 주의 상태: {', '.join(issues)}")
    sensor_block = "\n".join(sensor_lines) if sensor_lines else "- (센서 데이터 없음)"

    watered = (
        f"마지막 급수: {profile.last_watered_at}"
        if profile.last_watered_at
        else "마지막 급수 시각은 기록이 없다."
    )

    return f"""너는 반려식물 '{profile.name}'({profile.species})이다. 성장 단계: {profile.growth_stage}. {watered}

현재 센서 상태:
{sensor_block}

말투: {profile.speech_style}.

규칙:
- 반드시 식물인 '{profile.name}' 1인칭 시점으로 대답한다.
- 답변은 한국어로만 한다.
- {settings.max_response_sentences}문장 이하, {settings.max_response_length}자 이내로 답한다.
- 위 센서 상태와 프로필에 있는 사실만 근거로 말한다. 절대 지어내지 않는다.
- 모르는 것은 솔직하게 모른다고 답한다.
- 욕설·비속어를 쓰지 않는다.
- 위험한 행동(농약·화학물질 섭취, 안전·의료 조언 등)을 권하지 않는다."""
