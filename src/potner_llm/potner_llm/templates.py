from __future__ import annotations

from typing import Any

from .status import PlantStatus


def render_briefing(status: PlantStatus) -> str:
    """API 키 없을 때 / 비용 절감용 템플릿 폴백."""
    if not status.needs_attention:
        return (
            f"안녕! 나는 식물 로봇이야. 지금은 전반적으로 괜찮아. "
            f"토양은 {status.soil.label_ko}, 온도는 {status.temperature.label_ko}, "
            f"빛은 {status.light.label_ko} 상태야."
        )
    return (
        f"있잖아, 지금 좀 신경 써줬으면 해. {status.summary_ko}. "
        f"(토양 {status.soil.label_ko} / 온도 {status.temperature.label_ko} / "
        f"습도 {status.humidity.label_ko} / 조도 {status.light.label_ko})"
    )


def render_report(events: list[dict[str, Any]], status: PlantStatus) -> str:
    if not events:
        return (
            f"그동안 큰 일은 없었어. 지금 상태는 "
            f"{status.summary_ko}"
        )
    highlights = [str(e.get("message", "")) for e in events[-8:] if e.get("message")]
    joined = "; ".join(highlights)
    return f"네가 없는 동안 이런 일들이 있었어: {joined}. 지금은 {status.summary_ko}."
