"""웹 정보 조회 — 날씨(Open-Meteo)·식물 지식(한국어 위키백과)·뉴스(Google News RSS).

LLM 툴(get_weather / search_plant_knowledge / get_news)의 데이터 소스.
임의 URL은 긁지 않는다 — SSRF·프롬프트 인젝션·품질 문제를 피하려고
키가 필요 없는 구조화 엔드포인트 3개로 한정한다.

모든 공개 메서드는 예외를 밖으로 던지지 않고 dict를 돌려준다
(SensorDataProvider와 같은 실패 흡수 정책 — 툴 결과가 곧 LLM 입력이라
실패도 "설명 가능한 데이터"여야 한다).
"""

from __future__ import annotations

import json
import logging
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from typing import Any, Optional

logger = logging.getLogger(__name__)

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
WIKI_API_URL = "https://ko.wikipedia.org/w/api.php"
NEWS_RSS_URL = "https://news.google.com/rss?hl=ko&gl=KR&ceid=KR:ko"

# 위키백과 등은 기본 urllib User-Agent(Python-urllib)를 403으로 차단한다(실측).
# 위키미디어 UA 정책대로 서비스명+연락처를 밝힌다.
USER_AGENT = "PotnerPlantRobot/0.1 (SSAFY project; contact: repo maintainer)"

# 툴 결과가 곧 프롬프트 토큰이다 — 길이를 소스 단계에서 자른다.
KNOWLEDGE_EXTRACT_MAX_CHARS = 500
KNOWLEDGE_MAX_RESULTS = 2
FORECAST_DAYS = 3

# WMO weather interpretation codes (Open-Meteo 문서 기준)
_WMO_CODE_LABELS = {
    0: "맑음",
    1: "대체로 맑음",
    2: "구름 조금",
    3: "흐림",
    45: "안개",
    48: "안개(착빙)",
    51: "약한 이슬비",
    53: "이슬비",
    55: "강한 이슬비",
    61: "약한 비",
    63: "비",
    65: "강한 비",
    66: "어는 비",
    67: "강한 어는 비",
    71: "약한 눈",
    73: "눈",
    75: "강한 눈",
    77: "싸락눈",
    80: "약한 소나기",
    81: "소나기",
    82: "강한 소나기",
    85: "소낙눈",
    86: "강한 소낙눈",
    95: "뇌우",
    96: "뇌우(우박 동반)",
    99: "강한 뇌우(우박 동반)",
}


def _sky_label(code: Any) -> str:
    try:
        return _WMO_CODE_LABELS.get(int(code), "알 수 없음")
    except (TypeError, ValueError):
        return "알 수 없음"


class WebProvider:
    """웹 조회 파사드. 날씨·뉴스는 실패 시 마지막 성공값을 재사용한다."""

    def __init__(
        self,
        *,
        latitude: float,
        longitude: float,
        timeout_seconds: float = 8.0,
        news_limit: int = 5,
    ) -> None:
        self.latitude = latitude
        self.longitude = longitude
        self.timeout_seconds = timeout_seconds
        self.news_limit = news_limit
        self._last_weather: Optional[dict[str, Any]] = None
        self._last_news: Optional[dict[str, Any]] = None

    # --- 날씨 ---

    def weather(self) -> dict[str, Any]:
        params = urllib.parse.urlencode(
            {
                "latitude": self.latitude,
                "longitude": self.longitude,
                "current": "temperature_2m,relative_humidity_2m,precipitation,weather_code",
                "daily": "temperature_2m_max,temperature_2m_min,"
                "precipitation_probability_max,weather_code",
                "timezone": "Asia/Seoul",
                "forecast_days": FORECAST_DAYS,
            }
        )
        try:
            data = self._get_json(f"{OPEN_METEO_URL}?{params}")
        except Exception as exc:
            logger.warning(f"날씨 조회 실패: {exc}")
            if self._last_weather is not None:
                return {**self._last_weather, "안내": "방금 조회가 안 돼서 조금 전에 확인한 값이에요."}
            return {"error": "weather_unavailable", "안내": "지금은 바깥 날씨를 확인할 수 없어요."}

        current = data.get("current") or {}
        daily = data.get("daily") or {}
        forecast = []
        dates = daily.get("time") or []
        for i, date in enumerate(dates[:FORECAST_DAYS]):
            forecast.append(
                {
                    "날짜": date,
                    "최저_c": _nth(daily.get("temperature_2m_min"), i),
                    "최고_c": _nth(daily.get("temperature_2m_max"), i),
                    "강수확률_pct": _nth(daily.get("precipitation_probability_max"), i),
                    "하늘": _sky_label(_nth(daily.get("weather_code"), i)),
                }
            )
        result = {
            "출처": "open-meteo (바깥 날씨 — 내 센서값이 아님)",
            "현재_바깥": {
                "기온_c": current.get("temperature_2m"),
                "습도_pct": current.get("relative_humidity_2m"),
                "강수_mm": current.get("precipitation"),
                "하늘": _sky_label(current.get("weather_code")),
            },
            "일별예보": forecast,
        }
        self._last_weather = result
        logger.info(f"날씨 조회 성공: 현재 {result['현재_바깥']}")
        return result

    # --- 식물 지식 ---

    def search_knowledge(self, query: str) -> dict[str, Any]:
        query = (query or "").strip()
        if not query:
            return {"error": "empty_query", "안내": "무엇을 찾아볼지 검색어를 알려줘요."}
        params = urllib.parse.urlencode(
            {
                "action": "query",
                "generator": "search",
                "gsrsearch": query,
                "gsrlimit": KNOWLEDGE_MAX_RESULTS,
                "prop": "extracts",
                "exintro": 1,
                "explaintext": 1,
                "format": "json",
            }
        )
        try:
            data = self._get_json(f"{WIKI_API_URL}?{params}")
        except Exception as exc:
            logger.warning(f"지식 검색 실패 ({query!r}): {exc}")
            return {"error": "search_unavailable", "안내": "지금은 검색이 안 돼요. 잠시 뒤에 다시 물어봐 줘요."}

        pages = ((data.get("query") or {}).get("pages") or {}).values()
        results = []
        for page in sorted(pages, key=lambda p: p.get("index", 99)):
            extract = (page.get("extract") or "").strip()
            if not extract:
                continue
            results.append(
                {
                    "제목": page.get("title", ""),
                    "요약": extract[:KNOWLEDGE_EXTRACT_MAX_CHARS],
                }
            )
        if not results:
            return {"출처": "wikipedia", "결과": [], "안내": f"'{query}' 관련 문서를 못 찾았어요."}
        logger.info(f"지식 검색 성공 ({query!r}): {len(results)}건")
        return {"출처": "wikipedia (일반 지식 — 내 상태가 아님)", "결과": results}

    # --- 뉴스 ---

    def news(self) -> dict[str, Any]:
        try:
            request = urllib.request.Request(
                NEWS_RSS_URL,
                headers={"Accept": "application/rss+xml", "User-Agent": USER_AGENT},
            )
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                root = ET.fromstring(response.read())
        except Exception as exc:
            logger.warning(f"뉴스 조회 실패: {exc}")
            if self._last_news is not None:
                return {**self._last_news, "안내": "방금 조회가 안 돼서 조금 전에 확인한 소식이에요."}
            return {"error": "news_unavailable", "안내": "지금은 새 소식을 가져올 수 없어요."}

        headlines = []
        for item in root.iter("item"):
            title = (item.findtext("title") or "").strip()
            if not title:
                continue
            headlines.append({"제목": title, "언론사": (item.findtext("source") or "").strip()})
            if len(headlines) >= self.news_limit:
                break
        result = {"출처": "google-news (세상 소식 — 내 이야기가 아님)", "헤드라인": headlines}
        self._last_news = result
        logger.info(f"뉴스 조회 성공: {len(headlines)}건")
        return result

    # --- 내부 ---

    def _get_json(self, url: str) -> dict[str, Any]:
        request = urllib.request.Request(
            url, headers={"Accept": "application/json", "User-Agent": USER_AGENT}
        )
        with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
            return json.loads(response.read().decode("utf-8"))


def _nth(values: Any, index: int) -> Any:
    if isinstance(values, list) and 0 <= index < len(values):
        return values[index]
    return None


def create_web_provider(config: dict[str, Any] | None = None) -> Optional[WebProvider]:
    """config(llm.yaml의 web 섹션)에서 provider를 만든다.

    config 예시::

        web:
          enabled: true
          latitude: 37.50
          longitude: 127.04
          timeout_seconds: 8
          news_limit: 5

    web 섹션이 없거나 enabled가 아니면 None(호출부가 툴 비활성 안내로 폴백).
    잘못된 설정은 조용히 넘기지 않고 시작 시점에 ValueError로 알린다 —
    create_sensor_provider와 같은 정책이다.
    """
    web_cfg = (config or {}).get("web") or {}
    if not bool(web_cfg.get("enabled", False)):
        return None
    try:
        latitude = float(web_cfg["latitude"])
        longitude = float(web_cfg["longitude"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("web.enabled=true에는 web.latitude/longitude(숫자)가 필요하다") from exc
    return WebProvider(
        latitude=latitude,
        longitude=longitude,
        timeout_seconds=float(web_cfg.get("timeout_seconds", 8.0)),
        news_limit=int(web_cfg.get("news_limit", 5)),
    )
