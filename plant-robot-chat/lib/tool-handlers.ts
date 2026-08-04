/**
 * 도구 핸들러. 서버 전용 (app/api/chat/route.ts 에서만 import).
 *
 * 원칙: 모든 핸들러는 절대 throw하지 않고 JSON 직렬화 가능한 값만 반환한다.
 * 모델은 tool_result 문자열을 그대로 읽으므로, 실패도 "설명 가능한 데이터"여야
 * 초록이가 사용자에게 상황을 말해줄 수 있다.
 */

import { PLANTS, findPlant, type PlantRecord } from '@/lib/plant-data';
import { isSensorReadingStale } from '@/lib/sensor-store';
import type { ClientLocation, SensorReading } from '@/types/chat';

/**
 * 모델 입력(input)과 별개로 서버가 알고 있는 컨텍스트.
 * 사용자가 직접 말하지 않아도 도구가 참조해야 하는 값들 — 최신 센서 캐시, 브라우저 위치,
 * 서버 기준 현재 시각(테스트에서 시각을 고정할 수 있도록 인자로 받는다).
 */
export interface ToolContext {
  sensorReading: SensorReading | null;
  location?: ClientLocation;
  now: Date;
}

// ------------------------------------------------------------ 입력 정규화 유틸
// 모델이 보내는 input은 스키마를 벗어날 수 있어(문자열 대신 배열 등) 방어적으로 좁힌다.

function asString(value: unknown): string | undefined {
  if (typeof value === 'string') {
    const trimmed = value.trim();
    return trimmed.length > 0 ? trimmed : undefined;
  }
  if (typeof value === 'number' && Number.isFinite(value)) return String(value);
  return undefined;
}

function asStringArray(value: unknown): string[] {
  if (Array.isArray(value)) {
    return value.map(asString).filter((v): v is string => typeof v === 'string');
  }
  const single = asString(value);
  return single ? [single] : [];
}

function asNumber(value: unknown): number | undefined {
  if (typeof value === 'number' && Number.isFinite(value)) return value;
  if (typeof value === 'string') {
    const parsed = Number(value.trim());
    if (Number.isFinite(parsed)) return parsed;
  }
  return undefined;
}

function asBoolean(value: unknown): boolean {
  if (typeof value === 'boolean') return value;
  if (typeof value === 'string') {
    const v = value.trim().toLowerCase();
    return v === 'true' || v === 'yes' || v === '예' || v === '있음' || v === 'y';
  }
  return false;
}

/** 키워드 포함 검사용. 공백/기호를 없애 '흰 가루'와 '흰가루'를 같게 본다. */
function normalizeForMatch(text: string): string {
  return text.toLowerCase().replace(/[\s.,!?~·・/()-]/g, '');
}

// ============================================================ search_plant_info

type InfoType = 'toxicity' | 'care_guide' | 'disease_diagnosis' | 'general_info';

function normalizeInfoType(raw: string | undefined): InfoType {
  if (!raw) return 'general_info';
  const key = normalizeForMatch(raw);
  if (key.includes('toxic') || key.includes('독성') || key.includes('반려동물')) return 'toxicity';
  if (key.includes('care') || key.includes('관리') || key.includes('키우')) return 'care_guide';
  if (key.includes('disease') || key.includes('diagnos') || key.includes('병') || key.includes('해충')) {
    return 'disease_diagnosis';
  }
  return 'general_info';
}

function shapePlantInfo(plant: PlantRecord, infoType: InfoType): Record<string, unknown> {
  const base = {
    found: true as const,
    displayName: plant.displayName,
    scientificName: plant.scientificName,
    infoType,
  };

  switch (infoType) {
    case 'toxicity':
      return {
        ...base,
        toxicToPets: plant.toxicToPets,
        toxicityNote: plant.toxicityNote,
        // 반려동물 관련 답변은 단정하지 않도록 모델에게 근거 한계를 알려준다.
        caution: '증상이 있으면 반드시 동물병원에 문의하도록 안내할 것',
      };

    case 'care_guide':
      return {
        ...base,
        wateringDays: plant.wateringDays,
        light: plant.light,
        lightLabel: plant.lightLabel,
        tempRange: plant.tempRange,
        humidity: plant.humidity,
        fertilizer: plant.fertilizer,
        difficulty: plant.difficulty,
      };

    case 'disease_diagnosis':
      return {
        ...base,
        commonPests: plant.commonPests,
        note: plant.note,
      };

    case 'general_info':
    default:
      return {
        ...base,
        wateringDays: plant.wateringDays,
        light: plant.light,
        lightLabel: plant.lightLabel,
        tempRange: plant.tempRange,
        humidity: plant.humidity,
        fertilizer: plant.fertilizer,
        commonPests: plant.commonPests,
        toxicToPets: plant.toxicToPets,
        toxicityNote: plant.toxicityNote,
        difficulty: plant.difficulty,
        traits: plant.traits,
        note: plant.note,
      };
  }
}

function searchPlantInfo(input: Record<string, unknown>): Record<string, unknown> {
  const name = asString(input.plant_name) ?? asString(input.plantName) ?? asString(input.name);
  const infoType = normalizeInfoType(asString(input.info_type) ?? asString(input.infoType));

  if (!name) {
    return { found: false, message: '정보를 찾지 못했습니다', reason: '식물 이름이 비어 있어요' };
  }

  const plant = findPlant(name);
  if (!plant) {
    return {
      found: false,
      message: '정보를 찾지 못했습니다',
      query: name,
      // 모델이 "모른다"로 끝내지 않고 대안을 제시하도록 유도한다.
      hint: '학명이나 다른 별칭으로 다시 검색하거나, 잎 모양·크기를 물어볼 것',
    };
  }

  return shapePlantInfo(plant, infoType);
}

// =================================================== diagnose_plant_condition

type Urgency = '낮음' | '보통' | '높음';

const URGENCY_RANK: Record<Urgency, number> = { '낮음': 1, '보통': 2, '높음': 3 };

interface PossibleCause {
  cause: string;
  explanation: string;
  remedy: string;
}

interface SymptomRule {
  label: string;
  keywords: string[];
  urgency: Urgency;
  causes: PossibleCause[];
}

/**
 * 증상 규칙표. 키워드는 normalizeForMatch 결과에 대해 부분일치로 검사하므로
 * 공백 없는 형태로 적는다.
 */
const SYMPTOM_RULES: SymptomRule[] = [
  {
    label: '잎이 노랗게 변함',
    keywords: ['노랑', '노란', '노랗', '누렇', '누레', '황변', '황색', 'yellow'],
    urgency: '보통',
    causes: [
      {
        cause: '과습',
        explanation: '흙이 계속 젖어 있으면 뿌리가 숨을 못 쉬어 아래쪽 잎부터 노랗게 떨어져요.',
        remedy: '겉흙이 마를 때까지 물을 멈추고, 화분 받침의 고인 물을 버리고 통풍시켜 주세요.',
      },
      {
        cause: '물부족',
        explanation: '수분이 모자라면 잎이 노랗게 얇아지고 가장자리가 함께 마릅니다.',
        remedy: '화분 아래로 물이 흘러나올 만큼 흠뻑 주고, 이후 겉흙 마름 정도로 주기를 정하세요.',
      },
      {
        cause: '영양결핍',
        explanation: '질소가 부족하면 오래된 잎이 전체적으로 균일하게 노랗게 바랩니다.',
        remedy: '성장기(봄·여름)에 묽게 희석한 액체 비료를 2~4주 간격으로 주세요.',
      },
      {
        cause: '직사광선',
        explanation: '강한 빛에 갑자기 노출되면 잎이 색이 빠지듯 노랗게 탈색됩니다.',
        remedy: '레이스 커튼 뒤 밝은 간접광으로 옮기고, 며칠에 걸쳐 서서히 적응시켜 주세요.',
      },
    ],
  },
  {
    label: '잎이 갈색으로 변함',
    keywords: ['갈색', '갈변', '브라운', 'brown', '끝이마', '가장자리마', '바삭', '탔', '타서'],
    urgency: '보통',
    causes: [
      {
        cause: '물부족·건조',
        explanation: '뿌리가 마르면 잎끝과 가장자리부터 바삭하게 갈색이 됩니다.',
        remedy: '물 주기를 앞당기고, 가습이나 자갈 트레이로 주변 습도를 올려 주세요.',
      },
      {
        cause: '직사광선(잎 화상)',
        explanation: '유리창을 통과한 강한 햇빛은 잎 표면을 국소적으로 태워 갈색 반점을 남깁니다.',
        remedy: '창에서 한두 걸음 떨어뜨리거나 얇은 커튼으로 빛을 걸러 주세요.',
      },
      {
        cause: '영양결핍·비료 과다',
        explanation: '비료가 과하면 염류가 뿌리를 상하게 해 잎끝이 타듯 갈색이 됩니다.',
        remedy: '비료를 한동안 멈추고, 물을 충분히 흘려 화분 속 염류를 씻어내 주세요.',
      },
      {
        cause: '과습으로 인한 뿌리 손상',
        explanation: '뿌리가 상하면 물을 못 올려 마른 것처럼 갈색으로 마릅니다.',
        remedy: '흙 상태를 손가락으로 확인하고, 젖어 있다면 물을 끊고 뿌리 상태를 살펴 주세요.',
      },
    ],
  },
  {
    label: '잎이 검게 변함',
    keywords: ['검정', '검게', '검은', '까맣', '까매', '흑색', '흑변', 'black'],
    urgency: '높음',
    causes: [
      {
        cause: '과습·뿌리썩음',
        explanation: '뿌리가 썩으면 줄기와 잎이 물기를 잔뜩 품은 채 검게 변해 갑니다.',
        remedy: '화분에서 꺼내 검고 무른 뿌리를 잘라내고, 마른 새 흙에 옮겨 심어 주세요.',
      },
      {
        cause: '저온·냉해',
        explanation: '찬 바람이나 10도 이하 환경에 닿은 부위가 검게 죽습니다.',
        remedy: '창가 냉기와 에어컨 바람을 피해 실내 안쪽으로 옮기고 온도를 유지하세요.',
      },
      {
        cause: '세균성 병해',
        explanation: '검은 부위가 빠르게 번지고 냄새가 나면 세균 감염일 수 있어요.',
        remedy: '감염 부위를 소독한 가위로 잘라내고, 잎에 물이 고이지 않게 하고 통풍시키세요.',
      },
    ],
  },
  {
    label: '잎이 처지고 시듦',
    keywords: ['처짐', '처져', '처지', '축늘', '늘어', '시들', '시듦', '기운없', '힘없', 'wilt', 'droop'],
    urgency: '높음',
    causes: [
      {
        cause: '수분부족',
        explanation: '세포가 물을 잃으면 잎과 줄기가 힘을 잃고 아래로 늘어집니다.',
        remedy: '흙 속 2~3cm가 말랐다면 흠뻑 관수하세요. 보통 몇 시간 안에 회복됩니다.',
      },
      {
        cause: '뿌리손상(과습·물리적 손상)',
        explanation: '흙은 젖었는데도 처져 있다면 뿌리가 상해 물을 못 빨아들이는 상태예요.',
        remedy: '물을 멈추고 뿌리를 확인해 무른 부분을 제거한 뒤 통기성 좋은 흙으로 바꿔 주세요.',
      },
      {
        cause: '급격한 환경 변화',
        explanation: '분갈이나 자리 이동 직후에는 일시적으로 몸살처럼 처질 수 있습니다.',
        remedy: '한 자리에 두고 직사광선과 비료를 피하며 1~2주 지켜봐 주세요.',
      },
    ],
  },
  {
    label: '잎에 반점이 생김',
    keywords: ['반점', '얼룩', '점이', '점들', '점생', 'spot'],
    urgency: '보통',
    causes: [
      {
        cause: '곰팡이성 반점병',
        explanation: '습하고 통풍이 나쁘면 갈색·검은 테두리를 가진 반점이 번집니다.',
        remedy: '병든 잎을 제거하고 잎에 직접 물 주기를 피하며, 필요하면 살균제를 쓰세요.',
      },
      {
        cause: '깍지벌레·해충 흔적',
        explanation: '잎 뒷면이나 잎맥에 붙은 해충이 즙을 빨아 점처럼 자국을 남깁니다.',
        remedy: '잎 뒤를 확인해 알코올 솜으로 닦아내고, 새 잎이 나올 때까지 격리하세요.',
      },
      {
        cause: '물방울 자국·잎 화상',
        explanation: '잎에 남은 물방울이 렌즈처럼 빛을 모아 동그란 자국을 만들 수 있어요.',
        remedy: '물은 흙에만 주고, 잎에 묻으면 부드럽게 닦아 주세요.',
      },
    ],
  },
  {
    label: '흰가루·흰 곰팡이가 생김',
    keywords: ['흰가루', '하얀가루', '백색가루', '흰곰팡이', '하얀곰팡이', '곰팡이', '솜같', '하얀솜', 'powder', 'mold', 'mildew'],
    urgency: '보통',
    causes: [
      {
        cause: '흰가루병',
        explanation: '잎 표면에 밀가루를 뿌린 듯한 흰 막이 생기는 곰팡이병이에요.',
        remedy: '심한 잎은 제거하고 통풍을 늘리며, 물은 아침에 흙에만 주세요.',
      },
      {
        cause: '깍지벌레(솜깍지)',
        explanation: '하얀 솜뭉치처럼 보이면 벌레가 잎겨드랑이에 자리 잡은 경우가 많습니다.',
        remedy: '면봉에 소독용 알코올을 묻혀 닦아내고, 일주일 간격으로 재확인하세요.',
      },
      {
        cause: '흙 표면 곰팡이(과습)',
        explanation: '흙만 하얗게 피었다면 물이 잦고 통풍이 부족하다는 신호예요.',
        remedy: '표면 흙을 걷어내고 물 주기를 늦추며, 창문을 열어 공기를 돌려 주세요.',
      },
    ],
  },
  {
    label: '성장이 멈춤',
    keywords: ['성장이멈', '성장멈', '자라지', '안자라', '크지않', '새잎이없', '멈췄', '멈춤', 'stunt', 'nogrowth'],
    urgency: '낮음',
    causes: [
      {
        cause: '뿌리막힘(화분이 작음)',
        explanation: '뿌리가 화분을 가득 채우면 더 자랄 공간이 없어 성장이 멈춥니다.',
        remedy: '배수구로 뿌리가 나왔는지 보고, 한 치수 큰 화분으로 분갈이해 주세요.',
      },
      {
        cause: '영양부족',
        explanation: '같은 흙에서 오래 지내면 양분이 소진돼 새 잎이 나오지 않습니다.',
        remedy: '성장기에 묽은 액체 비료를 정기적으로 주거나 흙을 새로 갈아 주세요.',
      },
      {
        cause: '광량부족',
        explanation: '빛이 모자라면 잎 간격이 벌어지고 새 잎이 작아지며 멈춘 듯 보입니다.',
        remedy: '창가 밝은 간접광으로 옮기거나 식물등을 하루 8~10시간 켜 주세요.',
      },
      {
        cause: '겨울 휴면',
        explanation: '기온과 일조가 줄어드는 계절에는 원래 성장이 느려집니다.',
        remedy: '휴면기에는 물과 비료를 줄이고 봄까지 기다려 주세요.',
      },
    ],
  },
  {
    label: '줄기가 무르고 연해짐',
    keywords: ['줄기가무', '줄기무', '물러', '무름', '짓무', '연해', '연하게', '흐물', 'soft', 'mushy'],
    urgency: '높음',
    causes: [
      {
        cause: '과습',
        explanation: '물이 과하면 줄기 밑동부터 물러져 힘없이 주저앉습니다.',
        remedy: '즉시 물을 끊고 통풍을 늘리며, 흙이 마르도록 밝은 곳에 두세요.',
      },
      {
        cause: '무름병(세균성)',
        explanation: '무른 부위가 갈색으로 번지고 냄새가 나면 세균성 무름병입니다.',
        remedy: '건강한 부위 위쪽을 소독한 칼로 잘라 삽목하고, 감염된 흙과 화분은 버리세요.',
      },
    ],
  },
];

function diagnosePlantCondition(input: Record<string, unknown>): Record<string, unknown> {
  const plantName = asString(input.plant_name) ?? asString(input.plantName);
  const symptoms = asStringArray(input.symptoms ?? input.symptom ?? input.description);

  if (symptoms.length === 0) {
    return {
      diagnoses: [],
      overallUrgency: '보통' satisfies Urgency,
      message: '증상 설명이 비어 있어요',
      hint: '잎 색 변화·처짐·반점·냄새 중 어떤 것인지, 언제부터인지 되물을 것',
    };
  }

  let worst = 0;
  const diagnoses = symptoms.map((symptom) => {
    const key = normalizeForMatch(symptom);
    const matched = SYMPTOM_RULES.filter((rule) => rule.keywords.some((kw) => key.includes(kw)));

    if (matched.length === 0) {
      // 매칭 실패도 빈손으로 돌려주지 않는다 — 모델이 무엇을 더 물어야 할지 알아야 한다.
      return {
        symptom,
        matched: false,
        possibleCauses: [
          {
            cause: '정보 부족',
            explanation: '설명만으로는 원인을 좁히기 어려워요.',
            remedy:
              '잎 색(노랑/갈색/검정), 처짐 여부, 반점·흰가루 유무, 마지막 물 준 날, 빛 환경을 알려주면 더 정확해요.',
          },
        ],
      };
    }

    worst = Math.max(worst, ...matched.map((rule) => URGENCY_RANK[rule.urgency]));

    const seen = new Set<string>();
    const possibleCauses: PossibleCause[] = [];
    for (const rule of matched) {
      for (const cause of rule.causes) {
        if (seen.has(cause.cause)) continue;
        seen.add(cause.cause);
        possibleCauses.push(cause);
      }
    }

    return {
      symptom,
      matched: true,
      matchedPatterns: matched.map((rule) => rule.label),
      urgency: matched.reduce<Urgency>(
        (acc, rule) => (URGENCY_RANK[rule.urgency] > URGENCY_RANK[acc] ? rule.urgency : acc),
        '낮음',
      ),
      possibleCauses,
    };
  });

  const overallUrgency: Urgency = worst >= 3 ? '높음' : worst === 2 ? '보통' : worst === 1 ? '낮음' : '보통';

  const urgencyAdvice: Record<Urgency, string> = {
    '높음': '오늘 안에 물 주기를 멈추고 뿌리·줄기 상태를 직접 확인하는 게 좋아요.',
    '보통': '며칠 안에 물 주기와 빛 환경을 조정하고 변화를 관찰해 주세요.',
    '낮음': '급하지 않아요. 환경을 조금씩 바꾸면서 2~3주 지켜보면 됩니다.',
  };

  return {
    plantName: plantName ?? null,
    plantKnown: plantName ? findPlant(plantName) !== null : false,
    diagnoses,
    overallUrgency,
    urgencyAdvice: urgencyAdvice[overallUrgency],
    disclaimer: '사진 없이 텍스트로 추정한 결과예요. 단정하지 말고 가능성으로 전달할 것',
  };
}

// ==================================================== get_seasonal_care_tips

type Season = 'spring' | 'summer' | 'autumn' | 'winter';
type PlantTypeKey = 'foliage' | 'succulent' | 'herb' | 'flowering';

const SEASON_LABEL: Record<Season, string> = {
  spring: '봄',
  summer: '여름',
  autumn: '가을',
  winter: '겨울',
};

const PLANT_TYPE_LABEL: Record<PlantTypeKey, string> = {
  foliage: '관엽식물',
  succulent: '다육·선인장',
  herb: '허브',
  flowering: '꽃식물',
};

function seasonOfMonth(month: number): Season {
  if (month >= 3 && month <= 5) return 'spring';
  if (month >= 6 && month <= 8) return 'summer';
  if (month >= 9 && month <= 11) return 'autumn';
  return 'winter';
}

/**
 * 스키마 enum 값 → 내부 키. tools.ts는 관엽식물을 'tropical'로 보내므로 반드시 매핑해야 한다
 * (부분일치 규칙만으로는 'tropical'이 어디에도 걸리지 않아 전체 팁으로 떨어진다).
 * 'general'은 유형 미지정이므로 의도적으로 null.
 */
const PLANT_TYPE_ENUM: Record<string, PlantTypeKey | null> = {
  tropical: 'foliage',
  foliage: 'foliage',
  succulent: 'succulent',
  herb: 'herb',
  flowering: 'flowering',
  general: null,
};

function normalizePlantType(raw: string | undefined): PlantTypeKey | null {
  if (!raw) return null;
  const key = normalizeForMatch(raw);
  if (key in PLANT_TYPE_ENUM) return PLANT_TYPE_ENUM[key];
  if (key.includes('다육') || key.includes('선인장') || key.includes('succulent') || key.includes('cact')) {
    return 'succulent';
  }
  if (key.includes('허브') || key.includes('herb') || key.includes('로즈마리') || key.includes('바질')) {
    return 'herb';
  }
  if (key.includes('꽃') || key.includes('개화') || key.includes('flower') || key.includes('bloom')) {
    return 'flowering';
  }
  if (key.includes('관엽') || key.includes('열대') || key.includes('foliage') || key.includes('잎')) {
    return 'foliage';
  }
  return null;
}

const SEASON_TIPS: Record<Season, Record<PlantTypeKey, string[]>> = {
  spring: {
    foliage: [
      '성장기 시작이라 물 주기를 겨울보다 한 단계 앞당겨도 됩니다.',
      '분갈이 최적기예요. 뿌리가 화분을 꽉 채웠으면 한 치수 큰 화분으로 옮기세요.',
      '묽은 액체 비료를 2~4주 간격으로 시작하세요.',
    ],
    succulent: [
      '겨울 단수에서 서서히 물을 늘리되, 흙이 완전히 마른 뒤에만 주세요.',
      '햇빛에 다시 적응시키는 시기 — 며칠에 걸쳐 노출을 늘리면 화상을 막을 수 있어요.',
      '웃자란 개체는 이 시기에 잘라 삽목하면 뿌리를 잘 냅니다.',
    ],
    herb: [
      '씨 뿌리기와 모종 심기에 가장 좋은 때예요.',
      '순지르기(적심)를 해주면 옆가지가 늘어 잎을 더 많이 수확할 수 있어요.',
      '햇빛을 하루 5시간 이상 받게 두면 향이 진해집니다.',
    ],
    flowering: [
      '꽃대가 올라오는 시기이므로 인산이 있는 비료를 주면 개화에 도움이 됩니다.',
      '시든 꽃은 바로 따주면 다음 꽃이 더 잘 올라옵니다.',
      '꽃봉오리가 생긴 뒤에는 자리를 자주 옮기지 마세요.',
    ],
  },
  summer: {
    foliage: [
      '증발이 빨라 물이 자주 필요하지만, 흙 상태를 손가락으로 확인한 뒤 주세요.',
      '에어컨 바람이 직접 닿으면 잎끝이 마릅니다. 바람길을 피해 배치하세요.',
      '한낮 유리창 직사광선은 잎을 태울 수 있으니 얇은 커튼으로 걸러 주세요.',
    ],
    succulent: [
      '장마철 고온다습이 가장 위험해요. 물을 크게 줄이고 통풍을 최우선으로 하세요.',
      '많은 다육이 한여름에는 성장을 멈추고 쉽니다. 비료를 멈추세요.',
      '받침에 고인 물은 즉시 버려야 뿌리썩음을 막을 수 있어요.',
    ],
    herb: [
      '아침에 물을 주면 한낮 뿌리 과열과 저녁 과습을 함께 피할 수 있어요.',
      '꽃대가 올라오면 잎이 질겨지니 꽃대를 잘라 잎 수확기를 늘리세요.',
      '라벤더·로즈마리는 장마에 특히 약해 비를 맞지 않는 곳으로 옮기세요.',
    ],
    flowering: [
      '고온기에는 꽃이 짧게 지므로 반그늘로 옮겨 개화 기간을 늘릴 수 있어요.',
      '잎에 물이 고인 채 밤을 넘기면 병이 생기기 쉬워요. 흙에만 주세요.',
      '통풍이 나쁘면 진딧물·흰가루병이 늘어납니다. 창을 자주 열어 주세요.',
    ],
  },
  autumn: {
    foliage: [
      '기온이 내려가면 물 마르는 속도도 느려집니다. 주기를 조금씩 늘리세요.',
      '비료는 이 시기까지만 주고 늦가을에는 멈추세요.',
      '해가 짧아지니 조금 더 밝은 자리로 옮겨 광량을 보충해 주세요.',
    ],
    succulent: [
      '가을은 다육의 두 번째 성장기예요. 물을 다시 조금 늘려도 됩니다.',
      '일교차가 커지면 단풍처럼 물드는 종이 많아요. 햇빛을 충분히 주세요.',
      '첫 추위 전에 실내로 들이고, 이후 물을 줄여 나가세요.',
    ],
    herb: [
      '월동 못하는 허브는 잎을 수확해 말리거나 냉동해 두세요.',
      '실내로 들일 허브는 미리 해충 확인 후 잎을 씻어 들이세요.',
      '가지를 3분의 1 정도 정리해 주면 실내에서 관리가 쉬워집니다.',
    ],
    flowering: [
      '봄에 꽃 볼 구근은 이 시기에 심어야 합니다.',
      '지고 난 꽃대와 마른 잎을 정리해 병해충 월동처를 없애 주세요.',
      '실내로 옮길 때 온도 차가 크지 않게 며칠 걸쳐 적응시키세요.',
    ],
  },
  winter: {
    foliage: [
      '휴면기라 물이 훨씬 천천히 마릅니다. 주기를 절반 수준으로 줄이세요.',
      '난방 때문에 공기가 매우 건조해요. 가습기나 자갈 트레이로 습도를 올려 주세요.',
      '밤에 창가는 급격히 차가워집니다. 잎이 유리에 닿지 않게 안쪽으로 옮기세요.',
      '비료는 주지 않는 게 안전합니다.',
    ],
    succulent: [
      '거의 단수해도 됩니다. 한 달에 한 번, 그마저도 흙이 완전히 말랐을 때만.',
      '가장 밝은 창가에 두되 유리에 잎이 닿지 않게 하세요(냉해).',
      '물을 준 뒤 저온이 겹치면 무릅니다. 따뜻한 낮에만 주세요.',
    ],
    herb: [
      '광량이 부족하면 웃자랍니다. 남향 창가나 식물등을 활용하세요.',
      '흙이 차갑고 젖은 상태가 오래가면 뿌리가 상합니다. 물을 줄이세요.',
      '수확은 조금씩만 — 겨울에는 새 잎이 느리게 나옵니다.',
    ],
    flowering: [
      '포인세티아·시클라멘처럼 겨울에 피는 식물은 서늘하고 밝은 곳을 좋아합니다.',
      '난방기 옆은 꽃이 빨리 집니다. 열원에서 떨어뜨려 주세요.',
      '개화 중에는 자리를 옮기지 않는 편이 꽃이 오래갑니다.',
    ],
  },
};

const SEASON_GENERAL_TIPS: Record<Season, string[]> = {
  spring: [
    '분갈이·번식·비료 재개에 가장 좋은 계절이에요.',
    '해충도 함께 깨어납니다. 새 잎 뒷면을 주 1회 확인하세요.',
    '환기를 시작하되 찬 새벽 바람은 피하세요.',
  ],
  summer: [
    '물 주기보다 통풍이 더 중요한 계절입니다.',
    '장마철에는 흙이 마르지 않으니 손가락으로 확인한 뒤에만 주세요.',
    '한낮 직사광선과 에어컨 바람, 두 극단을 모두 피하세요.',
  ],
  autumn: [
    '물과 비료를 서서히 줄여 겨울 준비를 시작하세요.',
    '실내로 들일 식물은 해충 점검 후 들이세요.',
    '해가 짧아진 만큼 자리를 창가 쪽으로 옮겨 주세요.',
  ],
  winter: [
    '과습이 겨울철 실패 원인 1위예요. 물은 확실히 마른 뒤에만.',
    '난방 건조와 창가 냉기, 둘 다 조심하세요.',
    '먼지 쌓인 잎을 닦아주면 적은 빛도 더 잘 활용합니다.',
  ],
};

const MONTH_NOTES: Record<number, string> = {
  1: '가장 추운 달 — 창가 냉해와 과습만 피하면 대부분 무사히 넘깁니다.',
  2: '햇빛이 길어지기 시작해요. 분갈이 준비(흙·화분)를 해두면 좋습니다.',
  3: '본격 성장 시작. 분갈이와 비료를 재개할 시기예요.',
  4: '번식(삽목·포기나누기) 성공률이 가장 높은 달입니다.',
  5: '성장이 빨라 물이 금방 마릅니다. 주기를 다시 점검하세요.',
  6: '장마 시작 — 물 주기를 줄이고 통풍에 집중하세요.',
  7: '고온다습 절정. 뿌리썩음과 곰팡이병 경계가 필요합니다.',
  8: '폭염기 — 한낮 직사광선과 에어컨 바람을 함께 피하세요.',
  9: '더위가 꺾이며 두 번째 성장기가 옵니다. 관리 강도를 회복하세요.',
  10: '실내로 들일 식물을 정리하고 해충을 점검할 달입니다.',
  11: '비료를 멈추고 물 주기를 늘려가며 휴면 준비를 하세요.',
  12: '난방 건조 시작 — 습도 관리가 가장 중요한 달입니다.',
};

function getSeasonalCareTips(input: Record<string, unknown>): Record<string, unknown> {
  const rawMonth = asNumber(input.month);
  // 모델이 month를 생략하는 경우가 흔해 서버 시각 기준 현재 월로 채운다.
  const month =
    rawMonth !== undefined && Number.isInteger(rawMonth) && rawMonth >= 1 && rawMonth <= 12
      ? rawMonth
      : new Date().getMonth() + 1;

  const season = seasonOfMonth(month);
  const typeKey = normalizePlantType(asString(input.plant_type) ?? asString(input.plantType));

  const tips = typeKey
    ? SEASON_TIPS[season][typeKey]
    : (Object.keys(SEASON_TIPS[season]) as PlantTypeKey[]).flatMap((key) =>
        SEASON_TIPS[season][key].slice(0, 2).map((tip) => `${PLANT_TYPE_LABEL[key]}: ${tip}`),
      );

  return {
    month,
    monthLabel: `${month}월`,
    season: SEASON_LABEL[season],
    plantType: typeKey ? PLANT_TYPE_LABEL[typeKey] : '전체',
    monthNote: MONTH_NOTES[month],
    tips,
    generalTips: SEASON_GENERAL_TIPS[season],
    usedCurrentMonth: rawMonth === undefined,
  };
}

// ========================================================== recommend_plant

type LightCondition = PlantRecord['light'];
type Difficulty = PlantRecord['difficulty'];
type Trait = PlantRecord['traits'][number];

const LIGHT_LABEL: Record<LightCondition, string> = {
  direct_sun: '직사광선이 드는 밝은 곳',
  bright_indirect: '밝은 간접광',
  low_light: '빛이 적은 곳',
};

const LIGHT_ENUM: Record<string, LightCondition> = {
  direct_sun: 'direct_sun',
  bright_indirect: 'bright_indirect',
  low_light: 'low_light',
};

/**
 * enum 값을 먼저 정확히 맞춰야 한다: 'bright_indirect'는 'direct'를 부분문자열로 포함하므로
 * 부분일치부터 하면 밝은 간접광이 직사광선으로 뒤집힌다(추천 결과가 전부 바뀜).
 * 뒤이은 부분일치 순서도 '간접'/'indirect'와 '반양지'가 '직사'/'양지'보다 앞이어야 한다.
 */
function normalizeLight(raw: string | undefined): LightCondition | null {
  if (!raw) return null;
  const key = normalizeForMatch(raw);
  const exact = LIGHT_ENUM[key];
  if (exact) return exact;

  if (key.includes('indirect') || key.includes('간접') || key.includes('반양지') || key.includes('창가')) {
    return 'bright_indirect';
  }
  if (key.includes('direct') || key.includes('직사') || key.includes('양지') || key.includes('full')) {
    return 'direct_sun';
  }
  if (key.includes('음지') || key.includes('어두') || key.includes('약한') || key.includes('그늘') || key.includes('lowlight')) {
    return 'low_light';
  }
  return null;
}

function normalizeExperience(raw: string | undefined): Difficulty | null {
  if (!raw) return null;
  const key = normalizeForMatch(raw);
  if (key.includes('begin') || key.includes('초보') || key.includes('입문') || key.includes('처음')) {
    return 'beginner';
  }
  if (key.includes('expert') || key.includes('전문') || key.includes('고수') || key.includes('숙련') || key.includes('advanced')) {
    return 'expert';
  }
  if (key.includes('inter') || key.includes('중급') || key.includes('보통')) return 'intermediate';
  return null;
}

const TRAIT_LABEL: Record<Trait, string> = {
  air_purifying: '공기정화',
  flowering: '꽃이 피는',
  edible: '먹을 수 있는',
  aesthetic: '인테리어용',
  low_maintenance: '손이 덜 가는',
};

const TRAIT_ENUM: Record<string, Trait> = {
  air_purifying: 'air_purifying',
  flowering: 'flowering',
  edible: 'edible',
  aesthetic: 'aesthetic',
  low_maintenance: 'low_maintenance',
};

/**
 * enum 값 정확일치를 먼저 본다: 'flowering'은 'low'를 부분문자열로 포함해서
 * 부분일치만 쓰면 꽃식물 요청에 low_maintenance 가점이 함께 붙어 순위가 왜곡된다.
 */
function normalizeTraits(value: unknown): Trait[] {
  const out = new Set<Trait>();
  for (const raw of asStringArray(value)) {
    const key = normalizeForMatch(raw);
    const exact = TRAIT_ENUM[key];
    if (exact) {
      out.add(exact);
      continue;
    }
    if (key.includes('공기') || key.includes('정화') || key.includes('air') || key.includes('purif')) {
      out.add('air_purifying');
    }
    if (key.includes('꽃') || key.includes('flower') || key.includes('bloom') || key.includes('개화')) {
      out.add('flowering');
    }
    if (key.includes('먹') || key.includes('식용') || key.includes('요리') || key.includes('허브') || key.includes('edible') || key.includes('herb')) {
      out.add('edible');
    }
    if (key.includes('인테리어') || key.includes('예쁜') || key.includes('예쁘') || key.includes('감성') || key.includes('미관') || key.includes('aesthet') || key.includes('decor')) {
      out.add('aesthetic');
    }
    // 'low' 단독으로 검사하면 'flowering'에 걸린다 — 'lowmaint'까지 붙여서 본다.
    if (key.includes('쉬운') || key.includes('쉽') || key.includes('초보') || key.includes('손이') || key.includes('게으') || key.includes('lowmaint') || key.includes('easy')) {
      out.add('low_maintenance');
    }
  }
  return [...out];
}

/**
 * 빛 조건 허용 범위. 저광량 식물은 밝은 간접광에서도 잘 자라므로 상향 호환을 허용하지만,
 * 그 반대(강광 요구 식물을 어두운 곳에)는 실패하므로 막는다.
 */
function lightFits(plantLight: LightCondition, wanted: LightCondition): boolean {
  if (wanted === 'direct_sun') return plantLight === 'direct_sun';
  if (wanted === 'bright_indirect') return plantLight === 'bright_indirect' || plantLight === 'low_light';
  return plantLight === 'low_light';
}

const DIFFICULTY_ALLOWED: Record<Difficulty, Difficulty[]> = {
  beginner: ['beginner'],
  intermediate: ['beginner', 'intermediate'],
  expert: ['beginner', 'intermediate', 'expert'],
};

const DIFFICULTY_LABEL: Record<Difficulty, string> = {
  beginner: '초보자도 쉬운',
  intermediate: '조금 손이 가는',
  expert: '난이도 있는',
};

function buildReason(plant: PlantRecord, wantedTraits: Trait[], hasPets: boolean): string {
  const parts: string[] = [`${plant.lightLabel} 환경에 잘 맞고 물은 ${plant.wateringDays} 주기예요`];

  const matched = plant.traits.filter((t) => wantedTraits.includes(t));
  if (matched.length > 0) {
    parts.push(`${matched.map((t) => TRAIT_LABEL[t]).join('·')} 특성이 원하신 조건과 겹쳐요`);
  }
  if (hasPets && !plant.toxicToPets) {
    parts.push('반려동물에게 알려진 독성이 없어 함께 두기 편해요');
  }
  parts.push(`${DIFFICULTY_LABEL[plant.difficulty]} 식물이에요`);

  return `${parts.join('. ')}.`;
}

function recommendPlant(input: Record<string, unknown>): Record<string, unknown> {
  const wantedLight = normalizeLight(asString(input.light_condition) ?? asString(input.lightCondition));
  const experience = normalizeExperience(asString(input.experience_level) ?? asString(input.experienceLevel));
  const hasPets = asBoolean(input.has_pets ?? input.hasPets);
  const wantedTraits = normalizeTraits(input.preference ?? input.preferences);

  // has_pets가 true면 독성 식물은 어떤 완화 단계에서도 복귀시키지 않는다 (안전 우선).
  const petSafe = (plant: PlantRecord): boolean => !(hasPets && plant.toxicToPets);
  const lightOk = (plant: PlantRecord): boolean => !wantedLight || lightFits(plant.light, wantedLight);
  const difficultyOk = (plant: PlantRecord): boolean =>
    !experience || DIFFICULTY_ALLOWED[experience].includes(plant.difficulty);

  let relaxedDifficulty = false;
  let relaxedLight = false;
  let pool = PLANTS.filter((plant) => petSafe(plant) && lightOk(plant) && difficultyOk(plant));

  // 최소 3종은 내놓아야 하므로 난이도 → 빛 순서로 단계적으로 완화한다.
  // 애초에 지정되지 않은 조건은 "넓혔다"고 말하면 거짓이 되므로 플래그를 세우지 않는다.
  if (pool.length < 3 && experience) {
    relaxedDifficulty = true;
    pool = PLANTS.filter((plant) => petSafe(plant) && lightOk(plant));
  }
  if (pool.length < 3 && wantedLight) {
    relaxedLight = true;
    pool = PLANTS.filter(petSafe);
  }
  const relaxed = relaxedDifficulty || relaxedLight;

  const scored = pool
    .map((plant, index) => {
      let score = 0;
      const matchedTraits = plant.traits.filter((t) => wantedTraits.includes(t));
      score += matchedTraits.length * 3;
      if (experience && plant.difficulty === experience) score += 2;
      if (experience === 'beginner' && plant.traits.includes('low_maintenance')) score += 2;
      if (hasPets && !plant.toxicToPets) score += 1;
      // 빛 조건을 완화한 경우에도 원래 조건에 맞는 후보가 위로 오도록 가점을 남긴다.
      if (wantedLight && lightFits(plant.light, wantedLight)) score += 2;
      if (wantedLight && plant.light === wantedLight) score += 1;
      return { plant, score, index };
    })
    .sort((a, b) => (b.score - a.score) || (a.index - b.index));

  const picked = scored.slice(0, 5);

  const recommendations = picked.map(({ plant }) => ({
    displayName: plant.displayName,
    scientificName: plant.scientificName,
    reason: buildReason(plant, wantedTraits, hasPets),
    oneLineTip: `${plant.lightLabel}에 두고 ${plant.wateringDays} 간격으로 물을 주세요.`,
  }));

  const notices: string[] = [];
  if (relaxedDifficulty) {
    notices.push(
      '조건을 모두 만족하는 식물이 적어서 난이도 조건을 조금 넓혔어요. 처음엔 손이 더 갈 수 있다는 점을 함께 알려줄 것',
    );
  }
  if (relaxedLight) {
    notices.push(
      '후보가 부족해 빛 조건까지 넓혔어요. 요청한 빛 환경과 맞지 않을 수 있으니 자리를 옮기거나 식물등이 필요할 수 있다고 알려줄 것',
    );
  }
  const notice = notices.length > 0 ? notices.join(' ') : undefined;

  return {
    count: recommendations.length,
    criteria: {
      light: wantedLight ? LIGHT_LABEL[wantedLight] : '지정 없음',
      experienceLevel: experience ?? '지정 없음',
      hasPets,
      preference: wantedTraits.map((t) => TRAIT_LABEL[t]),
    },
    relaxed,
    ...(notice ? { notice } : {}),
    recommendations,
    ...(recommendations.length === 0
      ? { message: '조건에 맞는 식물을 찾지 못했어요', hint: '빛 환경이나 반려동물 조건을 다시 확인해 되물을 것' }
      : {}),
  };
}

// ================================================== get_plant_sensor_status

/** 임계값은 몬스테라·포토스 등 실내 관엽식물 평균 기준의 대략치다 — 종별 특화는 하지 않는다. */
function labelSoilMoisture(percent: number): { label: string; advice: string } {
  if (percent < 20) return { label: '매우 건조함', advice: '오늘 안에 물을 흠뻑 주는 게 좋아요.' };
  if (percent < 40) return { label: '약간 건조함', advice: '조만간 물을 줄 준비를 하면 돼요.' };
  if (percent <= 70) return { label: '적당함', advice: '지금은 물을 주지 않아도 괜찮아요.' };
  return { label: '축축함(과습 주의)', advice: '당분간 물을 멈추고 흙이 마르길 기다려 주세요.' };
}

function labelTemperature(celsius: number): string {
  if (celsius < 10) return '식물에게 추운 편';
  if (celsius <= 28) return '대부분의 실내식물에게 적당한 편';
  return '더운 편(직사광선·환기 확인 필요)';
}

function labelHumidity(percent: number): string {
  if (percent < 30) return '건조한 편';
  if (percent <= 60) return '적당한 편';
  return '습한 편';
}

function labelLight(lux: number): string {
  if (lux < 200) return '어두운 편(저광량)';
  if (lux <= 2000) return '밝은 간접광 수준';
  return '직사광선에 가까운 강한 빛';
}

function getPlantSensorStatus(context: ToolContext): Record<string, unknown> {
  const reading = context.sensorReading;

  if (!reading) {
    return {
      available: false,
      message: '아직 로봇으로부터 받은 센서 값이 없어요',
      hint: '센서 연결 전이거나 첫 측정 전일 수 있다고 자연스럽게 설명할 것. 값을 추측해 말하지 말 것.',
    };
  }

  if (isSensorReadingStale(reading, context.now)) {
    return {
      available: false,
      measuredAt: reading.measuredAt,
      message: '마지막 측정이 너무 오래돼 지금 상태로 보기 어려워요',
      hint: '오래된 값이라 지금 상태를 확신할 수 없다고 말하고, 추측하지 말 것.',
    };
  }

  const result: Record<string, unknown> = { available: true, measuredAt: reading.measuredAt };

  if (reading.soilMoisturePercent !== undefined) {
    const { label, advice } = labelSoilMoisture(reading.soilMoisturePercent);
    result.soilMoisture = { percent: reading.soilMoisturePercent, label, advice };
  }
  if (reading.temperatureCelsius !== undefined) {
    result.temperature = {
      celsius: reading.temperatureCelsius,
      label: labelTemperature(reading.temperatureCelsius),
    };
  }
  if (reading.humidityPercent !== undefined) {
    result.humidity = { percent: reading.humidityPercent, label: labelHumidity(reading.humidityPercent) };
  }
  if (reading.lightLux !== undefined) {
    result.light = { lux: reading.lightLux, label: labelLight(reading.lightLux) };
  }

  return result;
}

// ======================================================== get_current_weather

interface CityCoord {
  label: string;
  latitude: number;
  longitude: number;
}

/** 좌표 하드코딩 — 지오코딩 API를 한 번 더 타면 실패 지점이 늘어나므로 피한다. */
const CITY_COORDS: Record<string, CityCoord> = {
  서울: { label: '서울', latitude: 37.5665, longitude: 126.978 },
  부산: { label: '부산', latitude: 35.1796, longitude: 129.0756 },
  대구: { label: '대구', latitude: 35.8714, longitude: 128.6014 },
  인천: { label: '인천', latitude: 37.4563, longitude: 126.7052 },
  광주: { label: '광주', latitude: 35.1595, longitude: 126.8526 },
  대전: { label: '대전', latitude: 36.3504, longitude: 127.3845 },
  울산: { label: '울산', latitude: 35.5384, longitude: 129.3114 },
  세종: { label: '세종', latitude: 36.48, longitude: 127.289 },
  수원: { label: '수원', latitude: 37.2636, longitude: 127.0286 },
  제주: { label: '제주', latitude: 33.4996, longitude: 126.5312 },
  춘천: { label: '춘천', latitude: 37.8813, longitude: 127.73 },
  강릉: { label: '강릉', latitude: 37.7519, longitude: 128.8761 },
  전주: { label: '전주', latitude: 35.8242, longitude: 127.148 },
  청주: { label: '청주', latitude: 36.6424, longitude: 127.489 },
  포항: { label: '포항', latitude: 36.019, longitude: 129.3435 },
  창원: { label: '창원', latitude: 35.228, longitude: 128.6811 },
};

/** 영문·별칭 → 표준 한글 키. 모델이 'Seoul'이나 '서울시'로 보낼 수 있다. */
const CITY_ALIASES: Record<string, string> = {
  seoul: '서울',
  busan: '부산',
  pusan: '부산',
  daegu: '대구',
  taegu: '대구',
  incheon: '인천',
  gwangju: '광주',
  kwangju: '광주',
  daejeon: '대전',
  taejon: '대전',
  ulsan: '울산',
  sejong: '세종',
  suwon: '수원',
  jeju: '제주',
  jejudo: '제주',
  cheju: '제주',
  chuncheon: '춘천',
  gangneung: '강릉',
  kangnung: '강릉',
  jeonju: '전주',
  chonju: '전주',
  cheongju: '청주',
  chongju: '청주',
  pohang: '포항',
  changwon: '창원',
  제주도: '제주',
  서울특별시: '서울',
};

function resolveCity(raw: string): CityCoord | null {
  const key = normalizeForMatch(raw);

  const aliased = CITY_ALIASES[key];
  if (aliased) return CITY_COORDS[aliased];

  // '서울시', '부산광역시', '수원시 영통구' 같은 입력도 받아들인다.
  for (const [name, coord] of Object.entries(CITY_COORDS)) {
    if (key.includes(name)) return coord;
  }
  for (const [alias, name] of Object.entries(CITY_ALIASES)) {
    if (key.includes(alias)) return CITY_COORDS[name];
  }
  return null;
}

/** 위경도(도) 사이의 대략적인 지표 거리. 도시 목록이 16개뿐이라 정밀 공식 없이 하버사인으로 충분하다. */
function haversineKm(a: { latitude: number; longitude: number }, b: CityCoord): number {
  const R = 6371;
  const dLat = ((b.latitude - a.latitude) * Math.PI) / 180;
  const dLon = ((b.longitude - a.longitude) * Math.PI) / 180;
  const lat1 = (a.latitude * Math.PI) / 180;
  const lat2 = (b.latitude * Math.PI) / 180;
  const h =
    Math.sin(dLat / 2) ** 2 + Math.cos(lat1) * Math.cos(lat2) * Math.sin(dLon / 2) ** 2;
  return 2 * R * Math.asin(Math.min(1, Math.sqrt(h)));
}

/**
 * 브라우저 Geolocation 좌표를 시스템 프롬프트에 노출할 때 쓰는 "가장 가까운 지원 도시" 근사.
 * 실제 날씨 조회는 이 근사 도시가 아니라 사용자의 원시 좌표로 한다(더 정확하다) — 이 함수는
 * 어디까지나 사람이 읽을 지명을 만들기 위한 용도.
 */
export function nearestSupportedCity(location: { latitude: number; longitude: number }): CityCoord {
  let best = CITY_COORDS.서울;
  let bestDist = Infinity;
  for (const coord of Object.values(CITY_COORDS)) {
    const dist = haversineKm(location, coord);
    if (dist < bestDist) {
      bestDist = dist;
      best = coord;
    }
  }
  return best;
}

const WEATHER_CODE_LABEL: Record<number, string> = {
  0: '맑음',
  1: '대체로 맑음',
  2: '구름 조금',
  3: '흐림',
  45: '안개',
  48: '서리 안개',
  51: '약한 이슬비',
  53: '이슬비',
  55: '강한 이슬비',
  56: '얼어붙는 약한 이슬비',
  57: '얼어붙는 이슬비',
  61: '약한 비',
  63: '비',
  65: '강한 비',
  66: '얼어붙는 약한 비',
  67: '얼어붙는 비',
  71: '약한 눈',
  73: '눈',
  75: '강한 눈',
  77: '싸락눈',
  80: '약한 소나기',
  81: '소나기',
  82: '강한 소나기',
  85: '약한 소낙눈',
  86: '강한 소낙눈',
  95: '천둥번개',
  96: '천둥번개와 약한 우박',
  99: '천둥번개와 강한 우박',
};

interface OpenMeteoResponse {
  current?: {
    temperature_2m?: unknown;
    relative_humidity_2m?: unknown;
    weather_code?: unknown;
  };
}

async function getCurrentWeather(
  input: Record<string, unknown>,
  context: ToolContext,
): Promise<Record<string, unknown>> {
  const supportedCities = Object.keys(CITY_COORDS);
  const raw = asString(input.city) ?? asString(input.location);

  // city를 못 찾아도 브라우저 위치가 있으면 원시 좌표로 바로 조회한다(가장 정확하다).
  let coord: CityCoord | null = raw ? resolveCity(raw) : null;
  let label = coord?.label;
  let latitude = coord?.latitude;
  let longitude = coord?.longitude;

  if (!coord && context.location) {
    latitude = context.location.latitude;
    longitude = context.location.longitude;
    label = `현재 위치 인근(${nearestSupportedCity(context.location).label})`;
  }

  if (latitude === undefined || longitude === undefined) {
    return { error: '도시를 찾지 못했습니다', query: raw, supportedCities };
  }

  const url =
    'https://api.open-meteo.com/v1/forecast' +
    `?latitude=${latitude}&longitude=${longitude}` +
    '&current=temperature_2m,relative_humidity_2m,weather_code';

  try {
    // 채팅 응답이 무한정 대기하지 않도록 10초에서 끊는다.
    const response = await fetch(url, {
      signal: AbortSignal.timeout(10_000),
      headers: { accept: 'application/json' },
      cache: 'no-store',
    });

    if (!response.ok) {
      return { error: '날씨 정보를 가져오지 못했습니다', city: label, status: response.status };
    }

    const data = (await response.json()) as OpenMeteoResponse;
    const temperature = asNumber(data.current?.temperature_2m);
    const humidity = asNumber(data.current?.relative_humidity_2m);
    const code = asNumber(data.current?.weather_code);

    if (temperature === undefined) {
      return { error: '날씨 정보를 가져오지 못했습니다', city: label, reason: '응답 형식이 예상과 다릅니다' };
    }

    return {
      city: label,
      temperature,
      humidity: humidity ?? null,
      condition: code !== undefined ? (WEATHER_CODE_LABEL[code] ?? '알 수 없음') : '알 수 없음',
    };
  } catch (error) {
    const aborted = error instanceof Error && (error.name === 'TimeoutError' || error.name === 'AbortError');
    return {
      error: aborted ? '날씨 정보를 가져오는 데 시간이 너무 오래 걸렸습니다' : '날씨 정보를 가져오지 못했습니다',
      city: label,
    };
  }
}

// ================================================================== 웹 지식 검색 (위키백과)

/** 검색 결과가 곧 모델 입력 토큰이라 요약 길이·건수를 소스 단계에서 자른다. */
const WIKI_EXTRACT_MAX_CHARS = 500;
const WIKI_MAX_RESULTS = 2;

interface WikiPage {
  title?: unknown;
  extract?: unknown;
  index?: unknown;
}

async function searchWebKnowledge(
  input: Record<string, unknown>,
): Promise<Record<string, unknown>> {
  const query = asString(input.query)?.trim();
  if (!query) return { error: '검색어가 비어 있습니다' };

  const url =
    'https://ko.wikipedia.org/w/api.php' +
    `?action=query&generator=search&gsrsearch=${encodeURIComponent(query)}` +
    `&gsrlimit=${WIKI_MAX_RESULTS}&prop=extracts&exintro=1&explaintext=1&format=json`;

  try {
    const response = await fetch(url, {
      signal: AbortSignal.timeout(10_000),
      headers: { accept: 'application/json' },
      cache: 'no-store',
    });
    if (!response.ok) {
      return { error: '검색 결과를 가져오지 못했습니다', query, status: response.status };
    }

    const data = (await response.json()) as { query?: { pages?: Record<string, WikiPage> } };
    const results = Object.values(data.query?.pages ?? {})
      .sort((a, b) => (asNumber(a.index) ?? 99) - (asNumber(b.index) ?? 99))
      .map((page) => ({
        title: asString(page.title) ?? '',
        summary: (asString(page.extract) ?? '').trim().slice(0, WIKI_EXTRACT_MAX_CHARS),
      }))
      .filter((entry) => entry.summary);

    if (results.length === 0) {
      return { source: '위키백과', query, results: [], note: '관련 문서를 찾지 못했습니다' };
    }
    return { source: '위키백과', query, results };
  } catch (error) {
    const aborted =
      error instanceof Error && (error.name === 'TimeoutError' || error.name === 'AbortError');
    return {
      error: aborted ? '검색이 너무 오래 걸렸습니다' : '검색 결과를 가져오지 못했습니다',
      query,
    };
  }
}

// ================================================================== 뉴스 헤드라인

const NEWS_RSS_URL = 'https://news.google.com/rss?hl=ko&gl=KR&ceid=KR:ko';
const NEWS_MAX_HEADLINES = 5;

/** RSS는 XML 파서 의존성 없이 item/title/source만 뽑는다 (헤드라인 용도로 충분). */
function decodeXmlText(text: string): string {
  return text
    .replace(/<!\[CDATA\[([\s\S]*?)\]\]>/g, '$1')
    .replace(/&quot;/g, '"')
    .replace(/&#39;|&apos;/g, "'")
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&amp;/g, '&')
    .trim();
}

async function getNews(): Promise<Record<string, unknown>> {
  try {
    const response = await fetch(NEWS_RSS_URL, {
      signal: AbortSignal.timeout(10_000),
      headers: { accept: 'application/rss+xml' },
      cache: 'no-store',
    });
    if (!response.ok) {
      return { error: '뉴스를 가져오지 못했습니다', status: response.status };
    }

    const xml = await response.text();
    const headlines: Array<{ title: string; source: string }> = [];
    for (const match of xml.matchAll(/<item>([\s\S]*?)<\/item>/g)) {
      const item = match[1];
      const title = decodeXmlText(/<title>([\s\S]*?)<\/title>/.exec(item)?.[1] ?? '');
      const source = decodeXmlText(/<source[^>]*>([\s\S]*?)<\/source>/.exec(item)?.[1] ?? '');
      if (title) headlines.push({ title, source });
      if (headlines.length >= NEWS_MAX_HEADLINES) break;
    }

    if (headlines.length === 0) {
      return { error: '뉴스를 가져오지 못했습니다', reason: '응답 형식이 예상과 다릅니다' };
    }
    return { source: '구글 뉴스', headlines };
  } catch (error) {
    const aborted =
      error instanceof Error && (error.name === 'TimeoutError' || error.name === 'AbortError');
    return { error: aborted ? '뉴스를 가져오는 데 시간이 너무 오래 걸렸습니다' : '뉴스를 가져오지 못했습니다' };
  }
}

// ================================================================== 디스패치

/**
 * 도구 실행 단일 진입점. 여기서 throw가 나가면 스트리밍 루프가 죽으므로
 * 어떤 경우에도 값으로 돌려준다.
 */
export async function runTool(
  name: string,
  input: Record<string, unknown>,
  context: ToolContext,
): Promise<unknown> {
  try {
    const args = input ?? {};

    switch (name) {
      case 'search_plant_info':
        return searchPlantInfo(args);
      case 'diagnose_plant_condition':
        return diagnosePlantCondition(args);
      case 'get_seasonal_care_tips':
        return getSeasonalCareTips(args);
      case 'recommend_plant':
        return recommendPlant(args);
      case 'get_current_weather':
        return await getCurrentWeather(args, context);
      case 'search_web_knowledge':
        return await searchWebKnowledge(args);
      case 'get_news':
        return await getNews();
      case 'get_plant_sensor_status':
        return getPlantSensorStatus(context);
      default:
        return { error: `알 수 없는 도구입니다: ${name}` };
    }
  } catch (error) {
    const detail = error instanceof Error ? error.message : String(error);
    return { error: `도구 실행 중 문제가 생겼습니다 (${name}): ${detail}` };
  }
}
