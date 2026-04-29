# Daily Brief - GitHub Actions

매일 KST 07:00 한국·미국 주식시장 자동 브리핑을 텔레그램으로 발송합니다.

## 설계 원칙
- 시세는 Yahoo Finance API 직접 호출 (AI 안 거침, 100% 정확)
- 해설만 AI 교차검증 (Gemini + OpenRouter 모델)
- AI는 숫자 생성 금지, 해설만 담당 (프롬프트 강제)
- 발송 전 팩트체크: AI가 만든 숫자가 원본과 1% 이상 차이나면 경고
- 모르는 정보는 "확인 필요"로 표기

## 필요한 시크릿 (Settings -> Secrets and variables -> Actions)
- GEMINI_API_KEY (Google AI Studio)
- OPENROUTER_API_KEY (openrouter.ai)
- TELEGRAM_BOT_TOKEN (BotFather)
- TELEGRAM_CHAT_ID (@userinfobot)

값은 절대 README/코드에 평문으로 두지 말 것. Secrets에만 저장.

## 수동 실행
Actions 탭 -> Daily Brief -> Run workflow

## 자동 스케줄
매일 KST 07:00 (UTC 22:00 전날). 변경하려면 .github/workflows/daily-brief.yml 의 cron 수정.

## 비용
모든 구성 요소 무료 한도 내. 월 0원.

## 보안 주의
- 시크릿은 GitHub Secrets에만 저장
- 키가 코드/문서에 들어간 채로 commit 시 OpenRouter 등이 자동 revoke 함
- 키 노출 시 즉시 회전(rotate)
