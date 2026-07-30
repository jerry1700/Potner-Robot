"""응답 파이프라인에서 오가는 데이터 모델.

DB가 아직 없으므로 PlantProfile/SensorSnapshot은 어디서 오든(모의 데이터,
파일, 추후 DB) 같은 형태로 서비스에 주입되도록 dataclass로 고정한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass(frozen=True)
class PlantProfile:
    """식물 프로필 — DB 또는 Mock에서 조회되는 정적 정보."""

    name: str = "초록이"
    species: str = "스킨답서스"
    speech_style: str = "다정하고 명랑한 반말"
    growth_stage: str = "성장기"
    last_watered_at: Optional[str] = None  # ISO 8601


@dataclass(frozen=True)
class SensorSnapshot:
    """최근 센서 스냅샷. 값이 None이면 해당 센서 데이터가 없다는 뜻."""

    soil: Optional[float] = None        # 토양 수분 %
    temp: Optional[float] = None        # 온도 °C
    humidity: Optional[float] = None    # 습도 %
    light: Optional[float] = None       # 조도 lux
    co2: Optional[float] = None         # CO₂ ppm
    photo_summary: Optional[str] = None  # 마지막 촬영 분석 결과 (있다면)

    def with_defaults(self, defaults: dict[str, Any]) -> "SensorSnapshot":
        """비어 있는 값을 기본값으로 채운 새 스냅샷을 돌려준다."""
        return SensorSnapshot(
            soil=self.soil if self.soil is not None else defaults.get("soil"),
            temp=self.temp if self.temp is not None else defaults.get("temp"),
            humidity=self.humidity if self.humidity is not None else defaults.get("humidity"),
            light=self.light if self.light is not None else defaults.get("light"),
            co2=self.co2 if self.co2 is not None else defaults.get("co2"),
            photo_summary=self.photo_summary,
        )

    def summary_dict(self) -> dict[str, Any]:
        """JSON 응답의 sensor_summary에 넣을 축약 dict (None은 제외)."""
        raw = {
            "soil": self.soil,
            "temp": self.temp,
            "humidity": self.humidity,
            "light": self.light,
            "co2": self.co2,
        }
        return {k: v for k, v in raw.items() if v is not None}


@dataclass(frozen=True)
class ChatResult:
    """서비스가 최종 반환하는 결과. to_dict()가 API 응답 JSON 형태."""

    success: bool
    message: str
    plant_name: str
    timestamp: str
    sensor_summary: dict[str, Any] = field(default_factory=dict)
    fallback: bool = False           # 검증 실패/LLM 오류로 기본 응답을 쓴 경우 True
    error_code: Optional[str] = None  # 입력 오류 등 실패 사유 코드

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "success": self.success,
            "message": self.message,
            "plant_name": self.plant_name,
            "timestamp": self.timestamp,
            "sensor_summary": self.sensor_summary,
        }
        if self.fallback:
            payload["fallback"] = True
        if self.error_code is not None:
            payload["error_code"] = self.error_code
        return payload
