# Git Convention

이 문서는 팀의 Git 커밋 메시지, 브랜치 네이밍, 협업 규칙을 정의합니다. 모든 팀원은 아래 규칙을 준수해야 합니다.

---

## 1. 커밋 메시지 컨벤션

### 1.1 기본 형식

```
<type>(<scope>): <subject>

<body>

<footer>
```

- **scope**는 선택 사항이며, 변경된 모듈/디렉토리명을 명시합니다.
- **body**와 **footer**는 필요할 때만 작성합니다.
- 제목과 본문 사이에는 반드시 빈 줄을 하나 둡니다.

### 1.2 Type 목록

| Type | 설명 |
|------|------|
| `feat` | 새로운 기능 추가 |
| `fix` | 버그 수정 |
| `docs` | 문서 수정 |
| `style` | 코드 포맷팅, 세미콜론 누락 등 (로직 변경 없음) |
| `refactor` | 코드 리팩토링 (기능 변화 없음) |
| `test` | 테스트 코드 추가/수정 |
| `chore` | 빌드 설정, 패키지 매니저 등 잡일 |
| `perf` | 성능 개선 |
| `ci` | CI 설정 변경 |
| `build` | 빌드 시스템, 외부 종속성 관련 |
| `revert` | 이전 커밋 되돌리기 |

### 1.3 작성 규칙

- 제목은 **50자 이내**로 간결하게 작성합니다.
- 제목 끝에 **마침표(.)를 사용하지 않습니다.**
- 제목은 **명령형**으로 작성합니다. (예: "수정함" ❌ → "수정" ⭕)
- 본문은 **무엇을, 왜** 변경했는지 설명합니다. (어떻게는 코드가 보여줍니다)
- 한 커밋에는 **하나의 논리적 변경사항**만 포함합니다.
- 커밋 메시지 언어는 **한글**로 통일합니다. *(팀 규칙에 맞게 수정하세요)*

### 1.4 Breaking Change 표기

호환성이 깨지는 변경은 타입 뒤에 `!`를 붙이거나 footer에 `BREAKING CHANGE:`를 명시합니다.

```
feat!: API 응답 구조 변경

BREAKING CHANGE: /users 엔드포인트의 응답 필드명이 user_id → id 로 변경됨
```

### 1.5 이슈 트래커 연동

```
Closes #123
Refs #456
Fixes #789
```

### 1.6 예시

```
feat(auth): 로그인 시 JWT 토큰 발급 기능 추가

- refresh token 로직 포함
- 만료 시간 1시간으로 설정

Closes #123
```

```
fix(cart): 수량 0일 때 장바구니 삭제 안 되던 버그 수정
```

```
docs(readme): 설치 가이드에 환경변수 설정 방법 추가
```

### 1.7 커밋 전 확인

`git add -A`처럼 한 번에 담을 때는 커밋 전에 `git status`로 무엇이
스테이징됐는지 반드시 확인합니다. 관련 없는 파일(임시 산출물, 실수로
지운 문서 등)이 같이 딸려가면 커밋 타입도 같이 어긋납니다 — 예를 들어
문서 하나를 실수로 지운 채 `feat` 커밋에 같이 담으면, 그 삭제는 `docs`로
처리됐어야 할 변경이 `feat` 커밋 이력 속에 묻힙니다.

---

## 2. 브랜치 네이밍 컨벤션

```
<작업브랜치>-<type>/<작업내용>/<ticket번호>
```

### 2.1 작업 브랜치

변경 대상에 따라 아래 작업 브랜치 중 하나를 선택합니다.

| 작업 브랜치 | 담당 영역 |
|------------|----------|
| `App-master` | Flutter 모바일 앱 |
| `Server-master` | Spring Boot 서버 |
| `Robot-master` | Jetson Orin Nano 로봇 |
| `Raspberry-master` | Raspberry Pi 5 |

### 2.2 Type 목록

| Type | 설명 |
|------|------|
| `feature` | 새로운 기능 개발 |
| `fix` | 버그 수정 |
| `hotfix` | 운영 환경의 긴급 오류 수정 |
| `refactor` | 기능 변화 없는 코드 구조 개선 |
| `docs` | 문서 추가 또는 수정 |
| `test` | 테스트 코드 추가 또는 수정 |
| `chore` | 설정, 의존성 등 기타 작업 |
| `release` | 배포 및 릴리스 준비 |

### 2.3 작성 규칙

- 브랜치명은 반드시 **`<작업브랜치>-<type>/<작업내용>/<ticket번호>`** 형식을 사용합니다.
- `작업브랜치`는 변경 대상에 맞는 기준 브랜치명을 그대로 사용합니다.
- `type`은 Type 목록 중 하나를 소문자로 작성합니다.
- `작업내용`은 영문 소문자와 하이픈(`-`)을 사용해 간결하게 작성합니다.
- `ticket번호`는 생략하지 않고 팀 이슈 트래커의 실제 티켓 번호를 작성합니다.
- 새 브랜치는 해당 작업 브랜치의 최신 상태에서 생성합니다.

### 2.4 예시

```text
App-feature/login-screen/123
Server-fix/token-expiration/124
Robot-feature/object-detection/125
Raspberry-chore/device-setup/126
```

잘못된 예시:

```text
feature/login-screen               # 작업 브랜치와 티켓 번호 누락
Server-master-fix/token-expiration # 티켓 번호 누락
Robot-master-Feature/camera/125     # type에 대문자 사용
App-master-feature/login_screen/123 # 작업 내용에 언더스코어 사용
```

---

## 3. PR(Pull Request) 규칙

- PR 제목도 커밋 컨벤션과 동일한 형식을 따릅니다.
- PR 설명에는 다음을 포함합니다.
  - 변경 사항 요약
  - 테스트 방법
  - 관련 이슈 번호
- 최소 1인 이상의 코드 리뷰 승인 후 머지합니다.
- 머지 방식은 **Squash and Merge**를 기본으로 합니다. *(팀 규칙에 맞게 수정하세요)*

---

## 4. 자동화 도구 설정

팀 전체가 규칙을 일관되게 지키도록 아래 도구를 사용합니다.

### 4.1 commitlint + husky (커밋 시 자동 검사)

컨벤션에 맞지 않는 커밋 메시지는 커밋 자체가 차단됩니다.

```bash
npm install --save-dev @commitlint/cli @commitlint/config-conventional husky
npx husky init
echo "npx --no -- commitlint --edit \$1" > .husky/commit-msg
```

`commitlint.config.js`:
```js
module.exports = {
  extends: ['@commitlint/config-conventional'],
  rules: {
    'type-enum': [2, 'always',
      ['feat', 'fix', 'docs', 'style', 'refactor', 'test', 'chore', 'perf', 'ci', 'build', 'revert']
    ],
    'subject-max-length': [2, 'always', 50],
  }
};
```

> `.husky/`와 `commitlint.config.js`를 레포에 커밋해두면, 팀원이 `git clone` 후 `npm install`만 해도 hook이 자동 적용됩니다. (`package.json`의 `prepare: "husky"` 스크립트 필요)

### 4.2 commitizen (대화형 작성 도구, 선택)

```bash
npm install --save-dev commitizen cz-conventional-changelog
```

```json
"scripts": { "commit": "cz" },
"config": {
  "commitizen": { "path": "cz-conventional-changelog" }
}
```

팀원은 `git commit` 대신 `npm run commit`을 실행해 대화형으로 메시지를 작성합니다.

### 4.3 GitHub Actions (PR 단계 이중 검사)

로컬 hook은 `--no-verify`로 우회 가능하므로, CI에서도 검사합니다.

`.github/workflows/commitlint.yml`:
```yaml
name: Lint Commit Messages
on: [pull_request]
jobs:
  commitlint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - uses: wagoid/commitlint-github-action@v6
```

브랜치 보호 규칙(`Require status checks to pass`)과 연결하면 컨벤션 위반 시 머지가 차단됩니다.

---

## 5. 자주 하는 실수 체크리스트

- [ ] type을 빠뜨리지 않았는가?
- [ ] 제목이 50자를 넘지 않는가?
- [ ] 제목 끝에 마침표가 없는가?
- [ ] 하나의 커밋에 여러 작업이 섞여있지 않은가?
- [ ] 이슈 번호를 footer에 명시했는가? (해당 시)
- [ ] 브랜치명이 `<작업브랜치>-<type>/<작업내용>/<ticket번호>` 형식인가?
- [ ] 변경 대상에 맞는 작업 브랜치에서 분기했는가?
- [ ] 브랜치명에 실제 티켓 번호를 포함했는가?

---

## 6. 참고

- [Conventional Commits 공식 문서](https://www.conventionalcommits.org/)
- [commitlint](https://commitlint.js.org/)
- [Angular Commit Message Guidelines](https://github.com/angular/angular/blob/main/CONTRIBUTING.md#commit)
