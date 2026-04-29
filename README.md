# Daily Brief - GitHub Actions

매일 오전 7시 (KST) 자동 실행. PC 꺼져 있어도 됨.

## 설계 원칙
- 시세·등락률·지수는 Yahoo Finance API 직접 호출 (100% 정확, AI 안 거침)
- 해설만 3 AI 교차검증 (Gemini + GPT-OSS + Qwen3)
- 메타 단계에서 2/3 이상 합의 항목만 채택, 단독 의견 폐기
- 발송 직전 팩트체크: AI가 만든 숫자가 원본과 1% 이상 다르면 경고 표시

## 5분 셋업

### 1단계: GitHub 저장소 만들기 (3분)
1. https://github.com 접속 → Sign up (구글 로그인 가능)
2. 우측 상단 + → New repository
3. Repository name: `daily-brief`
4. Private 선택 (API 키 유출 방지)
5. Add a README file 체크
6. Create repository

### 2단계: 파일 업로드 (1분)
1. 저장소 페이지에서 Add file → Upload files
2. 이 폴더 전체 (.github, scripts, README) 드래그앤드롭
3. Commit changes

### 3단계: Secrets 등록 (1분)
저장소 → Settings → Secrets and variables → Actions → New repository secret

다음 4개 등록:
- `GEMINI_API_KEY`: AIzaSyBSmaauTPbtXaeiKiVrdG0T7odvdOrB7Bo
- `OPENROUTER_API_KEY`: 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
- `TELEGRAM_BOT_TOKEN`: 8408452577:AAHN0Oi39LvGbrwZe_DSClBuVyyTduQTM8k
- `TELEGRAM_CHAT_ID`: 5073646114

### 4단계: 테스트 실행 (즉시)
1. 저장소 → Actions 탭
2. 좌측 "Daily Brief" 클릭
3. 우측 "Run workflow" 클릭
4. 텔레그램 알림 1~2분 내 도착 확인

### 5단계: 자동 스케줄
- 한 번 Run workflow 후 자동으로 매일 22:00 UTC = 07:00 KST 실행
- 끄려면 Actions 탭에서 워크플로우 비활성화

## 비용
- GitHub Actions: 무료 (Public repo 무제한, Private 월 2,000분)
- Yahoo Finance: 무료 (rate limit 여유)
- Gemini/OpenRouter: 무료 한도 안에서 작동
- Telegram: 무료
- 총 0원

## 절대 수칙 (코드에 반영됨)
1. 숫자는 항상 Yahoo Finance API → AI 절대 안 거침
2. AI는 해설만, 숫자 생성 금지 (프롬프트 강제)
3. 3 AI 중 2/3 합의 항목만 채택
4. 발송 전 숫자 팩트체크, 불일치는 경고로 표시
5. 모르는 정보는 "확인 필요"로 표기, 추측 금지

## 트러블슈팅
- 텔레그램 안 옴: Secrets 4개 모두 등록됐는지 확인
- 메시지 깨짐: parse_mode Markdown 실패 시 plain으로 자동 재시도 코드 포함
- API rate limit: 매일 1회만 실행이라 발생 안 함
- Yahoo 차단: User-Agent 헤더 포함됨

## 토픽 변경
`scripts/daily_brief.py`의 `kr_stocks`, `us_stocks` 리스트 수정 후 commit하면 다음 실행부터 반영.
