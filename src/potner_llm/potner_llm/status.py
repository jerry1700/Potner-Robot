from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Optional


@dataclass(frozen=True)
class MetricLevel:
    name: str
    value: Optional[float]
    level: str  # low | normal | high | unknown
    label_ko: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PlantStatus:
    timestamp: str
    soil: MetricLevel
    temperature: MetricLevel
    humidity: MetricLevel
    light: MetricLevel
    summary_ko: str
    needs_attention: bool

    def to_prompt_dict(self) -> dict[str, Any]:
        """LLM에 넘길 압축 요약 (원시 센서값 최소화)."""
        return {
            "시각": self.timestamp,
            "토양수분": self.soil.label_ko,
            "온도": self.temperature.label_ko,
            "습도": self.humidity.label_ko,
            "조도": self.light.label_ko,
            "요약": self.summary_ko,
            "주의필요": self.needs_attention,
            "세부": {
                "토양수분_pct": self.soil.value,
                "온도_c": self.temperature.value,
                "습도_pct": self.humidity.value,
                "조도_lux": self.light.value,
            },
        }
