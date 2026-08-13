"""LLM 응답의 사실성 검증 — 응답 속 주장을 센서·프로필 데이터와 대조한다.

check_response(validator.py)가 '형식'을 보는 것과 달리, 여기서는 '내용'을 본다:
프롬프트 규칙 "센서 상태와 프로필에 있는 사실만 근거로 말한다"를 코드로 강제한다.

검증 항목:
1. 수치 주장 — 응답에서 단위가 붙은 숫자(%, °C/도, lux, ppm)를 추출해
   SensorSnapshot의 실제 값과 허용 오차 내인지 대조한다.
2. 지어낸 수치 — 해당 센서 값이 없는데(None) 수치를 말하면 날조로 본다.
   주의: 반드시 with_defaults() 적용 **전**의 원시 스냅샷을 넘겨야 한다 —
   기본값으로 채운 스냅샷을 넘기면 지어낸 값끼리 맞춰보는 셈이 된다.
3. 상태 주장 — 토양 수분 값과 정반대인 표현(바싹 말랐는데 "촉촉해" 등)을 잡는다.
4. 지어낸 이벤트 — 급수 기록이 없는데 "아까 물 줬잖아"처럼 급수를 언급하면 잡는다.

순수 로직만 있다 (LLM 호출·네트워크·rclpy 없음). 후처리(postprocess)가 문장을
자를 수 있으므로 검증은 후처리 **전** 원문 텍스트를 대상으로 하는 것이 안전하다.

재생성 루프는 호출자(service.py) 소관이다 — verify_response()로 판정만 받고,
next_action()으로 재시도/폴백을 결정하는 형태로 쓴다::

    fact = verify_response(raw_reply, raw_sensors, profile)
    action = next_action(fact, attempt=attempt)
    if action == ACTION_RETRY: ...   # LLM 재호출
    if action == ACTION_FALLBACK: ...  # 폴백 메시지 (무응답 금지 불변식 유지)
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Optional

from .models import PlantProfile, SensorSnapshot
from .status import PlantStatus

logger = logging.getLogger(__name__)

# next_action()이 돌려주는 판정 — 호출자는 이 세 값만 분기하면 된다
ACTION_PASS = "pass"
ACTION_RETRY = "retry"
ACTION_FALLBACK = "fallback"


@dataclass(frozen=True)
class FactCheckPolicy:
    """사실성 검증의 허용 오차·재시도 정책.

    LLM이 값을 반올림해 말하는 것("42.6%" → "43%쯤")은 정상이므로,
    오차는 반올림+표현 여유를 감안해 잡는다.
    """

    pct_tolerance: float = 5.0            # 토양수분·습도 절대 오차 (%p)
    temp_tolerance: float = 2.0           # 온도 절대 오차 (°C)
    light_tolerance_ratio: float = 0.25   # 조도 상대 오차
    light_tolerance_min: float = 50.0     # 조도 최소 절대 오차 (lux, 저조도에서 상대 오차가 과하게 좁아지는 것 방지)
    co2_tolerance_ratio: float = 0.2      # CO2 상대 오차
    co2_tolerance_min: float = 50.0       # CO2 최소 절대 오차 (ppm)
    soil_dry_below: float = 30.0          # 이 미만이면 '건조' — 젖었다는 주장과 상충
    soil_wet_above: float = 60.0          # 이 초과면 '축축' — 말랐다는 주장과 상충
    max_retries: int = 1                  # 검증 실패 시 LLM 재생성 허용 횟수


DEFAULT_POLICY = FactCheckPolicy()


@dataclass(frozen=True)
class FactIssue:
    """검증 실패 사유 하나. code는 로그·error_code용, detail은 사람이 읽는 설명."""

    code: str    # sensor_mismatch:<metric> | fabricated_value:<metric>
    #              | state_conflict:soil | fabricated_event:watering
    detail: str


@dataclass(frozen=True)
class FactCheckResult:
    """verify_response()의 판정 결과."""

    ok: bool
    issues: tuple[FactIssue, ...] = ()
    claims_checked: int = 0  # 대조를 시도한 주장 수 (0이면 검증할 주장이 없었다는 뜻)

    @property
    def issue_codes(self) -> tuple[str, ...]:
        return tuple(issue.code for issue in self.issues)


def snapshot_from_status(status: PlantStatus) -> SensorSnapshot:
    """dialogue 스택의 PlantStatus를 검증용 SensorSnapshot으로 변환한다.

    MetricLevel.value가 None(미측정)이면 그대로 None으로 남긴다 — 미측정
    값에 대한 수치 주장이 fabricated_value로 잡히게 하기 위해서다.
    """
    return SensorSnapshot(
        soil=status.soil.value,
        temp=status.temperature.value,
        humidity=status.humidity.value,
        light=status.light.value,
    )


# --- 수치 주장 추출 ---

# 단위가 붙은 숫자만 주장으로 본다. 단위 없는 숫자("3문장", "이틀")는 센서와
# 무관한 경우가 대부분이라 검사하지 않는다 (오탐 방지).
# '도'는 "5분 정도"류 오탐을 막기 위해 숫자에 바로 붙은 경우만 인정한다.
# 부호: '영하'와 '-'를 인정한다 — 실측이 영하일 때 정답("영하 5도")이
# +5로 파싱되어 오탐 기각되는 것을 막는다. '-'는 직전이 숫자·점이 아닐
# 때만 부호로 본다 ("50-60%" 같은 범위 표현을 음수로 오독하지 않도록).
_NUMBER_CLAIM = re.compile(
    r"(?P<sign>영하\s*|(?<![\d.])-)?"
    r"(?:(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>%|퍼센트|°C|℃|lux|룩스|럭스|ppm)"
    r"|(?P<value2>\d+(?:\.\d+)?)(?P<unit2>도))"
)

_UNIT_TO_METRIC = {
    "%": "percent",       # 토양수분/습도 중 어느 쪽인지는 문맥으로 판별
    "퍼센트": "percent",
    "°C": "temp",
    "℃": "temp",
    "도": "temp",
    "lux": "light",
    "룩스": "light",
    "럭스": "light",
    "ppm": "co2",
}

_METRIC_LABEL_KO = {
    "soil": "토양 수분",
    "humidity": "습도",
    "temp": "온도",
    "light": "조도",
    "co2": "CO2",
}

# % 주장의 대상 판별용 문맥 키워드 (숫자 앞 일부 구간에서 찾는다)
_SOIL_CONTEXT = re.compile(r"토양|흙|뿌리|수분")
_HUMIDITY_CONTEXT = re.compile(r"습도|공기|대기")

# 웹 툴(날씨·지식·뉴스) 결과에서 온 수치는 자기 센서 주장이 아니다 — 숫자 앞
# 일부 구간에 아래 문맥이 있으면 대조를 건너뛴다. 그렇지 않으면 "내일은 3도래"
# (예보)가 센서 온도와 달라 정답이 기각된다. 시스템 프롬프트가 외부 정보에
# 출처("바깥은/예보로는/뉴스에서는")를 밝히게 지시하는 것과 맞물리는 장치다.
# 문맥이 없으면 기존과 동일하게 검사한다 (센서 거짓말 방어는 유지).
_EXTERNAL_CONTEXT = re.compile(
    r"내일|모레|주말|예보|날씨|바깥|기온|최저|최고|강수|비 올|뉴스|적정 온도|권장"
)
_EXTERNAL_CONTEXT_WINDOW = 12

# --- 상태 주장 (토양 수분) ---

_DRY_CLAIM = re.compile(r"목말|목이 말|건조|말랐|메말|바싹|물이 부족|물이 필요")
_WET_CLAIM = re.compile(r"촉촉|축축|흠뻑|물이 충분|물은 충분")

# --- 지어낸 이벤트 (급수) ---

# 급수 기록이 없을 때 "언제 물을 줬다/마셨다"는 구체적 회상은 날조로 본다.
# 기록이 있을 때의 시점 불일치까지는 따지지 않는다 (자연어 시제 해석은 오탐이 많다).
_WATER_EVENT = re.compile(
    r"(오늘|어제|그제|아까|방금|조금 전|아침에|저녁에)[^.!?]{0,10}물[^.!?]{0,4}(줬|주었|마셨|먹었|받았)"
)


def verify_response(
    text: Optional[str],
    sensors: SensorSnapshot,
    profile: Optional[PlantProfile] = None,
    *,
    policy: FactCheckPolicy = DEFAULT_POLICY,
) -> FactCheckResult:
    """LLM 응답 속 주장을 센서·프로필과 대조해 판정을 돌려준다.

    sensors에는 with_defaults() 적용 **전**의 원시 스냅샷을 넘겨야 한다.
    text가 비어 있으면 대조할 주장이 없으므로 통과 처리한다 —
    빈 응답 자체는 check_response(validator.py)가 잡는다.
    """
    body = str(text).strip() if text else ""
    if not body:
        return FactCheckResult(ok=True)

    issues: list[FactIssue] = []
    checked = 0

    checked += _check_numeric_claims(body, sensors, policy, issues)
    checked += _check_soil_state_claims(body, sensors, policy, issues)
    checked += _check_watering_event_claims(body, profile, issues)

    result = FactCheckResult(ok=not issues, issues=tuple(issues), claims_checked=checked)
    if result.ok:
        logger.info(f"사실성 검증 통과 (대조한 주장 {checked}개)")
    else:
        logger.warning(
            f"사실성 검증 실패 ({', '.join(result.issue_codes)}) reply[:80]={body[:80]!r}"
        )
    return result


def next_action(
    result: FactCheckResult,
    attempt: int,
    policy: FactCheckPolicy = DEFAULT_POLICY,
) -> str:
    """검증 결과와 현재 시도 횟수(0부터)로 다음 행동을 결정한다.

    통과면 pass, 재시도 여유가 있으면 retry, 소진했으면 fallback.
    fallback 경로도 반드시 폴백 메시지로 끝나야 한다 — 무응답 금지 불변식.
    """
    if result.ok:
        return ACTION_PASS
    if attempt < policy.max_retries:
        return ACTION_RETRY
    return ACTION_FALLBACK


# --- 내부: 수치 주장 대조 ---


def _check_numeric_claims(
    body: str,
    sensors: SensorSnapshot,
    policy: FactCheckPolicy,
    issues: list[FactIssue],
) -> int:
    checked = 0
    for match in _NUMBER_CLAIM.finditer(body):
        context = body[max(0, match.start() - _EXTERNAL_CONTEXT_WINDOW):match.start()]
        if _EXTERNAL_CONTEXT.search(context):
            continue  # 바깥 날씨/일반 지식/뉴스 수치 — 내 센서 주장이 아니다
        raw_value = match.group("value") or match.group("value2")
        unit = match.group("unit") or match.group("unit2")
        value = float(raw_value)
        if match.group("sign"):
            value = -value
        metric = _UNIT_TO_METRIC[unit]
        checked += 1

        if metric == "percent":
            _check_percent_claim(body, match.start(), value, sensors, policy, issues)
        else:
            actual = getattr(sensors, metric)
            if actual is None:
                issues.append(FactIssue(
                    f"fabricated_value:{metric}",
                    f"{_METRIC_LABEL_KO[metric]} 데이터가 없는데 {value:g}{unit}라고 말함",
                ))
            elif not _within_tolerance(metric, value, actual, policy):
                issues.append(FactIssue(
                    f"sensor_mismatch:{metric}",
                    f"{_METRIC_LABEL_KO[metric]} 실제 {actual:g}인데 {value:g}{unit}라고 말함",
                ))
    return checked


def _check_percent_claim(
    body: str,
    start: int,
    value: float,
    sensors: SensorSnapshot,
    policy: FactCheckPolicy,
    issues: list[FactIssue],
) -> None:
    """% 주장은 문맥 키워드로 토양수분/습도를 가른다. 문맥이 없으면 둘 중
    하나라도 오차 내면 통과시킨다 (모호한 주장을 억지로 틀렸다고 하지 않는다)."""
    context = body[max(0, start - 12):start]
    if _SOIL_CONTEXT.search(context):
        candidates = [("soil", sensors.soil)]
    elif _HUMIDITY_CONTEXT.search(context):
        candidates = [("humidity", sensors.humidity)]
    else:
        candidates = [("soil", sensors.soil), ("humidity", sensors.humidity)]

    available = [(name, actual) for name, actual in candidates if actual is not None]
    if not available:
        metric = candidates[0][0] if len(candidates) == 1 else "percent"
        label = _METRIC_LABEL_KO.get(metric, "수분/습도")
        issues.append(FactIssue(
            f"fabricated_value:{metric}",
            f"{label} 데이터가 없는데 {value:g}%라고 말함",
        ))
        return

    if any(_within_tolerance(name, value, actual, policy) for name, actual in available):
        return

    name, actual = available[0]
    issues.append(FactIssue(
        f"sensor_mismatch:{name}",
        f"{_METRIC_LABEL_KO[name]} 실제 {actual:g}%인데 {value:g}%라고 말함",
    ))


def _within_tolerance(metric: str, claimed: float, actual: float, policy: FactCheckPolicy) -> bool:
    if metric in ("soil", "humidity"):
        return abs(claimed - actual) <= policy.pct_tolerance
    if metric == "temp":
        return abs(claimed - actual) <= policy.temp_tolerance
    if metric == "light":
        allowed = max(abs(actual) * policy.light_tolerance_ratio, policy.light_tolerance_min)
        return abs(claimed - actual) <= allowed
    if metric == "co2":
        allowed = max(abs(actual) * policy.co2_tolerance_ratio, policy.co2_tolerance_min)
        return abs(claimed - actual) <= allowed
    return True


# --- 내부: 상태 주장 대조 ---


def _check_soil_state_claims(
    body: str,
    sensors: SensorSnapshot,
    policy: FactCheckPolicy,
    issues: list[FactIssue],
) -> int:
    """토양 수분 값과 정반대인 건조/축축 표현을 잡는다.

    값이 없으면(None) 검사하지 않는다 — 근거 없는 감상 표현까지 막으면
    잡담형 답변이 전부 막히고, 수치 날조는 위의 수치 검사가 잡는다.
    """
    if sensors.soil is None:
        return 0

    checked = 0
    dry = _DRY_CLAIM.search(body)
    wet = _WET_CLAIM.search(body)
    # "건조한 날씨래", "바깥 공기가 건조하대"처럼 날씨/바깥 이야기의 건조·촉촉
    # 표현은 토양 상태 주장이 아니다 — 표현 주변 구간에 외부 문맥이 있으면 제외.
    if dry and _external_state_context(body, dry):
        dry = None
    if wet and _external_state_context(body, wet):
        wet = None
    if dry:
        checked += 1
        if sensors.soil > policy.soil_wet_above:
            issues.append(FactIssue(
                "state_conflict:soil",
                f"토양 수분 {sensors.soil:g}%인데 건조하다고 말함 ({dry.group(0)!r})",
            ))
    if wet:
        checked += 1
        if sensors.soil < policy.soil_dry_below:
            issues.append(FactIssue(
                "state_conflict:soil",
                f"토양 수분 {sensors.soil:g}%인데 촉촉하다고 말함 ({wet.group(0)!r})",
            ))
    return checked


def _external_state_context(body: str, match: "re.Match[str]") -> bool:
    """상태 표현(건조/촉촉) 주변이 날씨·바깥 이야기인지 판별한다.

    수치 주장과 달리 문맥 단서("날씨래")가 표현 뒤에 오는 경우가 많아
    앞뒤 양쪽 구간을 본다.
    """
    segment = body[max(0, match.start() - 8):match.end() + 8]
    return bool(re.search(r"날씨|바깥|공기|대기|내일|예보", segment))


# --- 내부: 지어낸 이벤트 대조 ---


def _check_watering_event_claims(
    body: str,
    profile: Optional[PlantProfile],
    issues: list[FactIssue],
) -> int:
    if profile is None:
        return 0
    match = _WATER_EVENT.search(body)
    if match is None:
        return 0
    if profile.last_watered_at is None:
        issues.append(FactIssue(
            "fabricated_event:watering",
            f"급수 기록이 없는데 급수를 회상함 ({match.group(0)!r})",
        ))
    return 1
