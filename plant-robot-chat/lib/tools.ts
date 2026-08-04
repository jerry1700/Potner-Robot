/**
 * 도구 정의 (Anthropic 형식: {name, description, input_schema}).
 *
 * description을 "언제 호출해야 하는가"로 쓴 이유: 최신 Opus 계열은 도구를 보수적으로
 * 고르기 때문에 기능 설명만 적으면 호출하지 않고 기억으로 답해버린다.
 * 트리거 조건을 description 안에 넣는 쪽이 시스템 프롬프트로 유도하는 것보다 효과가 크다.
 */

import type { ToolDefinitions, ToolStatusMap } from '@/types/chat';

export const TOOLS: ToolDefinitions = [
  {
    name: 'search_plant_info',
    description:
      '특정 식물의 관리 정보(물주기 주기, 적정 온도, 습도, 빛 조건, 비료, 흔한 병해충, 반려동물 독성)를 조회한다. ' +
      '사용자가 식물 이름을 언급하면서 "어떻게 키워?", "물 얼마나 줘?", "우리 고양이한테 괜찮아?", ' +
      '"이 식물 뭐야?" 같은 질문을 하면 답변을 쓰기 전에 반드시 먼저 호출한다. ' +
      '기억에 의존해 수치(물주기 일수·온도 범위 등)를 말하지 말고, 이 도구가 돌려준 값만 근거로 삼는다. ' +
      '식물 이름을 확실히 모르는 상태로 추측해서 답하는 것보다, 사용자가 말한 이름 그대로 넣어 호출하는 편이 낫다.',
    input_schema: {
      type: 'object',
      properties: {
        plant_name: {
          type: 'string',
          description:
            '사용자가 말한 식물 이름. 한글 별칭(예: "몬스테라", "산세베리아")이나 영문명 모두 그대로 넣는다.',
        },
        info_type: {
          type: 'string',
          enum: ['care_guide', 'disease_diagnosis', 'general_info', 'toxicity'],
          description:
            '조회 목적. 키우는 방법·물주기 질문은 care_guide, 병해충 관련은 disease_diagnosis, ' +
            '어떤 식물인지 소개는 general_info, 반려동물·아이 안전 질문은 toxicity.',
        },
      },
      required: ['plant_name'],
      additionalProperties: false,
    },
  },
  {
    name: 'diagnose_plant_condition',
    description:
      '눈에 보이는 증상을 근거로 식물 상태의 원인과 대처법을 추려낸다. ' +
      '사용자가 "잎이 노래졌어", "끝이 갈색으로 말라", "축 처졌어", "흰 가루가 생겼어", "작은 벌레가 붙었어" 처럼 ' +
      '증상을 하나라도 말하면 원인을 짐작해서 답하지 말고 먼저 호출한다. ' +
      '식물 이름이나 환경(빛·물주기·계절)을 모르더라도 증상만으로 호출할 수 있으니 정보가 부족하다고 호출을 미루지 않는다. ' +
      '대화에서 이미 알아낸 환경 정보가 있으면 environment에 함께 넣어 정확도를 올린다.',
    input_schema: {
      type: 'object',
      properties: {
        symptoms: {
          type: 'array',
          items: { type: 'string' },
          description:
            '사용자가 묘사한 증상을 짧은 구절로 나눠 담는다. 예: ["아래쪽 잎이 노랗게 변함", "잎 끝이 갈색으로 마름"].',
        },
        plant_name: {
          type: 'string',
          description: '알고 있으면 식물 이름. 모르면 생략한다.',
        },
        environment: {
          type: 'object',
          description: '대화에서 확인된 재배 환경. 확인된 항목만 채운다.',
          properties: {
            light: {
              type: 'string',
              description: '빛 조건 설명. 예: "남향 창가", "형광등만 있는 방".',
            },
            watering_frequency: {
              type: 'string',
              description: '현재 물주기. 예: "3일에 한 번", "겉흙 마르면".',
            },
            season: {
              type: 'string',
              description: '현재 계절 또는 시기. 예: "겨울", "장마철".',
            },
          },
          additionalProperties: false,
        },
      },
      required: ['symptoms'],
      additionalProperties: false,
    },
  },
  {
    name: 'get_seasonal_care_tips',
    description:
      '해당 월과 식물 유형에 맞는 계절별 관리 요령(물주기 조정, 온도 관리, 비료, 분갈이 시기, 주의할 병해충)을 알려준다. ' +
      '사용자가 "요즘", "이번 달", "겨울에는", "장마철에", "환절기" 처럼 시기를 언급하며 관리법을 물으면 호출한다. ' +
      '계절에 따라 정답이 완전히 달라지는 주제(겨울 물주기, 여름 직광, 봄 분갈이)는 일반론으로 답하지 말고 이 도구를 쓴다. ' +
      'month를 생략하면 현재 월이 쓰이므로, 사용자가 특정 달을 지정하지 않았다면 굳이 추측해 넣지 않는다.',
    input_schema: {
      type: 'object',
      properties: {
        month: {
          type: 'number',
          minimum: 1,
          maximum: 12,
          description: '조회할 월 (1~12). 사용자가 특정 달을 말한 경우에만 넣는다.',
        },
        plant_type: {
          type: 'string',
          enum: ['tropical', 'succulent', 'herb', 'flowering', 'general'],
          description:
            '식물 유형. 몬스테라·포토스 같은 관엽은 tropical, 선인장·다육은 succulent, ' +
            '로즈마리·바질은 herb, 꽃을 보는 식물은 flowering, 특정할 수 없으면 general.',
        },
      },
      required: [],
      additionalProperties: false,
    },
  },
  {
    name: 'recommend_plant',
    description:
      '사용자의 공간·경험·반려동물 여부·취향에 맞는 식물을 골라 추천한다. ' +
      '"뭐 키우면 좋을까?", "초보가 키우기 쉬운 거", "고양이 있어도 되는 식물", "어두운 방에서도 살아남는 식물", ' +
      '"공기정화 식물 추천" 같은 요청에 호출한다. 머릿속 목록을 나열하지 말고 이 도구 결과를 근거로 답한다. ' +
      '빛 조건(light_condition)은 추천 품질을 좌우하므로, 대화에 단서가 없으면 먼저 "창가는 어느 방향이에요?"처럼 ' +
      '한 번 물어보고 확인된 뒤에 호출한다. 반려동물 언급이 있으면 has_pets를 반드시 채운다.',
    input_schema: {
      type: 'object',
      properties: {
        light_condition: {
          type: 'string',
          enum: ['direct_sun', 'bright_indirect', 'low_light'],
          description:
            '놓을 자리의 빛. 남향 창가처럼 직광이 드는 곳은 direct_sun, 밝지만 직광은 아닌 곳은 bright_indirect, ' +
            '창이 멀거나 조명뿐인 곳은 low_light.',
        },
        experience_level: {
          type: 'string',
          enum: ['beginner', 'intermediate', 'expert'],
          description: '식물 키운 경험. 언급이 없으면 생략한다(임의로 beginner라고 단정하지 않는다).',
        },
        has_pets: {
          type: 'boolean',
          description: '강아지·고양이 등 반려동물과 같이 사는지. 언급됐다면 반드시 채운다.',
        },
        preference: {
          type: 'string',
          enum: ['air_purifying', 'flowering', 'edible', 'aesthetic', 'low_maintenance'],
          description:
            '가장 중요하게 여기는 조건. 공기정화는 air_purifying, 꽃은 flowering, 먹을 수 있는 허브는 edible, ' +
            '인테리어는 aesthetic, 손 덜 가는 것은 low_maintenance.',
        },
      },
      required: ['light_condition'],
      additionalProperties: false,
    },
  },
  {
    name: 'get_current_weather',
    description:
      '도시의 현재 기온·습도·날씨 상태를 조회한다. ' +
      '"오늘 베란다에 내놔도 될까?", "환기해도 괜찮아?", "지금 물 줘도 돼?", "밖에 두면 얼까?" 처럼 ' +
      '실제 날씨에 따라 조언이 달라지는 질문에는 반드시 호출한다. ' +
      '날씨를 짐작해서 답하면 식물이 냉해·과습을 입을 수 있으니, 도시를 알 수 없을 때만 사용자에게 어디 사는지 묻는다. ' +
      '시스템 프롬프트에 사용자 위치가 이미 안내돼 있다면 city를 그 지명으로 채워 바로 호출한다. ' +
      'city를 비워도 사용자 위치 정보가 있으면 서버가 좌표로 자동 조회한다.',
    input_schema: {
      type: 'object',
      properties: {
        city: {
          type: 'string',
          description:
            '도시 이름. 한글 그대로 넣는다. 예: "서울", "부산", "대전". ' +
            '시스템 프롬프트의 사용자 위치를 알고 있다면 생략하지 말고 그 지명을 넣는다.',
        },
      },
      required: [],
      additionalProperties: false,
    },
  },
  {
    name: 'search_web_knowledge',
    description:
      '한국어 위키백과에서 문서 요약을 검색한다. ' +
      'search_plant_info가 모르는 식물이라고 답했거나, 내부 데이터베이스 범위를 벗어나는 ' +
      '식물·원예 용어("수경재배", "테라리움", 희귀 품종 등)나 일반 지식 질문을 받으면 ' +
      '추측으로 답하기 전에 먼저 호출한다. 결과 요약을 근거로 삼고 출처가 위키백과임을 밝힌다.',
    input_schema: {
      type: 'object',
      properties: {
        query: {
          type: 'string',
          description: '검색어. 사용자가 말한 이름·용어를 한글 그대로 넣는다. 예: "테라리움", "칼라디움".',
        },
      },
      required: ['query'],
      additionalProperties: false,
    },
  },
  {
    name: 'get_news',
    description:
      '지금 이 순간의 주요 뉴스 헤드라인 목록을 가져온다. ' +
      '"오늘 뉴스 뭐 있어?", "요즘 세상에 무슨 일 있어?", "바깥 소식 궁금해" 같은 질문에 호출한다. ' +
      '기억 속 지식은 오래됐으니 최신 소식 질문에는 반드시 이 도구를 쓴다. ' +
      '결과는 헤드라인 전달 수준으로 짧게 요약하고, 정치·논쟁적 주제에는 의견을 붙이지 않는다.',
    input_schema: {
      type: 'object',
      properties: {},
      required: [],
      additionalProperties: false,
    },
  },
  {
    name: 'get_plant_sensor_status',
    description:
      '로봇에 달린 센서가 방금 측정한 흙 수분·온도·습도·조도 실측값을 조회하고 해석한다. ' +
      '"지금 흙 상태 어때?", "물 줘야 해?", "지금 온도 몇 도야?", "습도 괜찮아?", "빛 잘 들어와?", ' +
      '"우리 식물 지금 괜찮아?" 처럼 지금 이 순간의 실제 상태를 묻는 질문에는 기억이나 일반론으로 ' +
      '답하지 말고 반드시 이 도구를 먼저 호출한다. 센서가 없거나 값이 오래됐으면 도구가 그 사실을 알려주니 ' +
      '그 경우에는 "지금은 정확한 값을 모른다"고 솔직히 말할 것.',
    input_schema: {
      type: 'object',
      properties: {},
      required: [],
      additionalProperties: false,
    },
  },
];

/** 도구 실행 중 UI에 띄우는 진행 문구. TOOLS의 name과 키를 1:1로 맞춘다. */
export const TOOL_STATUS: ToolStatusMap = {
  search_plant_info: '🔍 식물 정보를 찾아보고 있어요...',
  diagnose_plant_condition: '🩺 증상을 살펴보고 있어요...',
  get_seasonal_care_tips: '🗓️ 계절 관리법을 챙겨보고 있어요...',
  recommend_plant: '🌱 어울리는 식물을 고르고 있어요...',
  get_current_weather: '☀️ 날씨를 확인하고 있어요...',
  search_web_knowledge: '📚 위키백과를 뒤져보고 있어요...',
  get_news: '📰 오늘의 소식을 살펴보고 있어요...',
  get_plant_sensor_status: '🌡️ 센서 값을 확인하고 있어요...',
};
