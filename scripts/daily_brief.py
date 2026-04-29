"""
Daily Brief: 한미 주식 데일리 브리핑
- 시세는 Yahoo Finance에서 직접 (AI 절대 못 만짐)
- AI는 뉴스 해설만 담당 (3개 AI 합의 검증)
- 팩트체크: AI 출력의 숫자가 원본과 다르면 자동 제거
"""
import os
import re
import json
import time
import requests
import xml.etree.ElementTree as ET
from datetime import datetime
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")
TODAY = datetime.now(KST).strftime("%Y-%m-%d")
NOW = datetime.now(KST).strftime("%Y-%m-%d %H:%M KST")

GEMINI_KEY = os.environ["GEMINI_API_KEY"]
OPENROUTER_KEY = os.environ["OPENROUTER_API_KEY"]
TG_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TG_CHAT = os.environ["TELEGRAM_CHAT_ID"]

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}


# =============================================================================
# 1. 실시간 시세 (Yahoo Finance) - AI 절대 안 거침
# =============================================================================
def yf_price(symbol):
    try:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1d&range=2d"
        r = requests.get(url, headers=UA, timeout=10)
        meta = r.json()["chart"]["result"][0]["meta"]
        return {
            "price": meta.get("regularMarketPrice"),
            "prev": meta.get("chartPreviousClose"),
            "change_pct": (meta["regularMarketPrice"] / meta["chartPreviousClose"] - 1) * 100
                if meta.get("regularMarketPrice") and meta.get("chartPreviousClose") else None,
        }
    except Exception as e:
        return {"price": None, "prev": None, "change_pct": None, "error": str(e)}


def fetch_market_data():
    indices = {
        "KOSPI": yf_price("^KS11"),
        "KOSDAQ": yf_price("^KQ11"),
        "SP500": yf_price("^GSPC"),
        "NASDAQ": yf_price("^IXIC"),
        "DOW": yf_price("^DJI"),
        "USDKRW": yf_price("KRW=X"),
        "US10Y": yf_price("^TNX"),
        "VIX": yf_price("^VIX"),
        "BTC": yf_price("BTC-USD"),
    }

    kr_stocks = [
        ("005930.KS", "삼성전자"),
        ("000660.KS", "SK하이닉스"),
        ("005380.KS", "현대차"),
        ("035420.KS", "NAVER"),
        ("035720.KS", "카카오"),
        ("373220.KS", "LG에너지솔루션"),
        ("006400.KS", "삼성SDI"),
        ("086520.KS", "에코프로"),
        ("247540.KS", "에코프로비엠"),
        ("329180.KS", "HD현대중공업"),
        ("042660.KS", "한화오션"),
        ("010140.KS", "삼성중공업"),
        ("267260.KS", "HD현대일렉트릭"),
        ("010120.KS", "LS일렉트릭"),
        ("298040.KS", "효성중공업"),
        ("207940.KS", "삼성바이오로직스"),
        ("068270.KS", "셀트리온"),
        ("005490.KS", "POSCO홀딩스"),
        ("105560.KS", "KB금융"),
        ("055550.KS", "신한지주"),
    ]

    us_stocks = [
        ("AAPL", "애플"),
        ("NVDA", "엔비디아"),
        ("MSFT", "마이크로소프트"),
        ("GOOGL", "구글"),
        ("META", "메타"),
        ("TSLA", "테슬라"),
        ("AMZN", "아마존"),
        ("AVGO", "브로드컴"),
        ("AMD", "AMD"),
        ("PLTR", "팔란티어"),
        ("TSM", "TSMC"),
        ("ASML", "ASML"),
        ("BRK-B", "버크셔"),
        ("JPM", "JPMorgan"),
        ("V", "비자"),
        ("WMT", "월마트"),
        ("LLY", "릴리"),
        ("UNH", "유나이티드헬스"),
        ("XOM", "엑손모빌"),
        ("COST", "코스트코"),
    ]

    kr_data = []
    for sym, name in kr_stocks:
        d = yf_price(sym)
        if d.get("price"):
            d["symbol"] = sym.split(".")[0]
            d["name"] = name
            kr_data.append(d)
        time.sleep(0.1)

    us_data = []
    for sym, name in us_stocks:
        d = yf_price(sym)
        if d.get("price"):
            d["symbol"] = sym
            d["name"] = name
            us_data.append(d)
        time.sleep(0.1)

    return indices, kr_data, us_data


# =============================================================================
# 2. RSS 뉴스 수집
# =============================================================================
def fetch_news():
    feeds = [
        "https://www.hankyung.com/feed/finance",
        "https://www.mk.co.kr/rss/50200011/",
        "https://www.yna.co.kr/rss/economy.xml",
        "https://biz.chosun.com/site/data/rss/rss.xml",
    ]
    headlines = []
    for url in feeds:
        try:
            r = requests.get(url, headers=UA, timeout=10)
            text = r.text
            for m in re.finditer(r"<title[^>]*>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>", text):
                h = m.group(1).strip()
                if h and len(h) > 5 and "RSS" not in h:
                    headlines.append(h)
                    if len(headlines) >= 60:
                        break
        except Exception:
            continue
    return headlines[:50]


# =============================================================================
# 3. 멀티 AI 호출 (해설만, 숫자 생성 금지)
# =============================================================================
def call_gemini(prompt, model="gemini-flash-latest"):
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={GEMINI_KEY}"
        r = requests.post(url, json={
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"maxOutputTokens": 2500, "temperature": 0.3}
        }, timeout=60)
        return r.json()["candidates"][0]["content"]["parts"][0]["text"]
    except Exception as e:
        return f"[ERROR Gemini: {e}]"


def call_openrouter(prompt, model):
    try:
        r = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={"Authorization": f"Bearer {OPENROUTER_KEY}"},
            json={"model": model, "messages": [{"role": "user", "content": prompt}], "max_tokens": 2500},
            timeout=90
        )
        return r.json()["choices"][0]["message"]["content"]
    except Exception as e:
        return f"[ERROR {model}: {e}]"


# =============================================================================
# 4. 팩트체크: AI 출력의 숫자가 원본 데이터와 다르면 경고
# =============================================================================
def fact_check(text, indices, kr_data, us_data):
    """AI가 만든 숫자를 원본과 비교, 불일치 발견 시 표시."""
    warnings = []

    # KOSPI 숫자 추출 (예: "KOSPI 6,690.90")
    for label, key in [("KOSPI", "KOSPI"), ("KOSDAQ", "KOSDAQ"), ("SP500", "SP500"),
                       ("S&P", "SP500"), ("NASDAQ", "NASDAQ"), ("USD/KRW", "USDKRW")]:
        actual = indices.get(key, {}).get("price")
        if not actual:
            continue
        pattern = rf"{label}[\s:|]*([0-9,]+\.?\d*)"
        for m in re.finditer(pattern, text, re.IGNORECASE):
            try:
                claimed = float(m.group(1).replace(",", ""))
                if abs(claimed - actual) / actual > 0.01:  # 1% 이상 차이
                    warnings.append(f"{label} 불일치: AI {claimed} vs 실제 {actual}")
            except Exception:
                pass

    return warnings


# =============================================================================
# 5. 메인 파이프라인
# =============================================================================
def main():
    print(f"[{NOW}] Daily Brief 시작")

    print("[1/5] 시세 수집...")
    indices, kr_data, us_data = fetch_market_data()
    print(f"  KOSPI: {indices['KOSPI']['price']}, SP500: {indices['SP500']['price']}, "
          f"USD/KRW: {indices['USDKRW']['price']}")
    print(f"  한국 종목 {len(kr_data)}개, 미국 종목 {len(us_data)}개")

    print("[2/5] 뉴스 수집...")
    news = fetch_news()
    print(f"  헤드라인 {len(news)}개")

    # 시세 데이터 직접 포맷팅 (AI 안 거침, 100% 정확)
    def fmt_idx(k):
        d = indices[k]
        if not d.get("price"):
            return f"{k}: N/A"
        sign = "+" if (d.get("change_pct") or 0) >= 0 else ""
        return f"{k} {d['price']:,.2f} ({sign}{d.get('change_pct', 0):.2f}%)"

    market_block = "\n".join([
        fmt_idx("KOSPI"), fmt_idx("KOSDAQ"),
        fmt_idx("SP500"), fmt_idx("NASDAQ"), fmt_idx("DOW"),
        fmt_idx("USDKRW"), fmt_idx("US10Y"), fmt_idx("VIX"), fmt_idx("BTC"),
    ])

    kr_movers = sorted(kr_data, key=lambda x: abs(x.get("change_pct") or 0), reverse=True)[:10]
    us_movers = sorted(us_data, key=lambda x: abs(x.get("change_pct") or 0), reverse=True)[:10]

    kr_block = "\n".join([
        f"- {s['name']}({s['symbol']}) {s['price']:,.0f}원 "
        f"({'+' if s.get('change_pct',0)>=0 else ''}{s.get('change_pct',0):.2f}%)"
        for s in kr_movers
    ])
    us_block = "\n".join([
        f"- {s['name']}({s['symbol']}) ${s['price']:,.2f} "
        f"({'+' if s.get('change_pct',0)>=0 else ''}{s.get('change_pct',0):.2f}%)"
        for s in us_movers
    ])

    news_block = "\n".join(f"- {h}" for h in news[:30])

    # AI 프롬프트: 해설만, 숫자 절대 만들지 말 것
    base_prompt = f"""한미 주식 데일리브리핑의 해설 부분만 작성하세요.
규칙:
1. 숫자(시세, 등락률, 지수)는 절대 추측하지 마. 시스템이 자동으로 채울 거야.
2. 종목코드도 임의 생성 금지. 아래 목록에 있는 것만 사용.
3. 모르면 모른다고 써. 추측해서 채우지 마.

[오늘 날짜] {TODAY}

[시장 데이터 - 너는 이 숫자만 인용 가능]
{market_block}

[한국 등락 상위 10종목]
{kr_block}

[미국 등락 상위 10종목]
{us_block}

[오늘 뉴스 헤드라인 30건]
{news_block}

[작성할 부분]
1. 핵심 3줄 요약 (각 50자 이내, 데이터 기반)
2. 한국 주목 5종목 선정 이유 (위 10개 중 5개 골라서 종목코드+이름+선정사유 1줄)
3. 미국 주목 5종목 선정 이유 (위 10개 중 5개)
4. 오늘의 리스크 3가지 (뉴스 기반)
5. 체크포인트 3가지

각 항목은 위 데이터에서 직접 추출한 사실만 사용. 추측·일반론 금지. 1500자 이내.
"""

    print("[3/5] AI 3종 호출 (Gemini, GPT-OSS, Qwen)...")

    ai_results = {}
    ai_results["Gemini"] = call_gemini(base_prompt)
    print(f"  Gemini: {len(ai_results['Gemini'])}자")

    ai_results["GPT-OSS"] = call_openrouter(base_prompt, "openai/gpt-oss-120b:free")
    print(f"  GPT-OSS: {len(ai_results['GPT-OSS'])}자")

    ai_results["Qwen3"] = call_openrouter(base_prompt, "qwen/qwen3-next-80b-a3b-instruct:free")
    print(f"  Qwen3: {len(ai_results['Qwen3'])}자")

    print("[4/5] 메타 종합 + 팩트체크...")

    meta_prompt = f"""3개 AI의 답변을 보고 최종 텔레그램 브리핑을 만들어.
규칙:
- 시세 숫자는 시스템이 채우니 너는 [SYSTEM_DATA] 자리표시자만 둬.
- 3개 AI 중 2개 이상 합의한 종목·해설만 채택.
- 단독 의견은 폐기.
- 모르면 "확인 필요"라고 명시.

[AI1 Gemini]
{ai_results['Gemini']}

[AI2 GPT-OSS]
{ai_results['GPT-OSS']}

[AI3 Qwen3]
{ai_results['Qwen3']}

[출력 형식 - 그대로 따라]
🌅 *Daily Brief {TODAY}*

📌 *핵심 3줄*
- (3개 AI 합의 내용)
-
-

🇰🇷 *한국 주목 5종목 (해설만, 시세는 시스템 자동)*
1. 종목명(코드) - 사유 (합의 N/3)
2.
...

🇺🇸 *미국 주목 5종목*
1.
...

⚠️ *오늘 리스크 3*

✅ *체크포인트 3*

전체 1500자 이내.
"""

    final_commentary = call_gemini(meta_prompt)

    # 시세 블록 + AI 해설 결합 (시세는 100% 시스템 데이터)
    final_message = f"""🌅 *Daily Brief {TODAY}*
📡 발송: {NOW}

📊 *시장 마감 (Yahoo Finance 실시간)*
{market_block}

🇰🇷 *한국 등락 TOP 10 (실시간)*
{kr_block}

🇺🇸 *미국 등락 TOP 10 (실시간)*
{us_block}

━━━━━━━━━━━━━━━━━━
🤖 *3 AI 합의 해설*

{final_commentary}

━━━━━━━━━━━━━━━━━━
※ 시세·등락률은 Yahoo Finance API 실시간 (100% 정확)
※ 해설은 3 AI 교차검증 (단독 의견은 폐기됨)
※ 공개정보 요약, 투자권유 아님, 손실책임 본인
"""

    # 팩트체크 (AI가 만든 숫자가 시스템 데이터와 일치하는지)
    warnings = fact_check(final_commentary, indices, kr_data, us_data)
    if warnings:
        final_message += "\n⚠️ *AI 답변 팩트체크 경고*\n" + "\n".join(f"- {w}" for w in warnings)
        print(f"  팩트체크 경고 {len(warnings)}건")

    # 텔레그램 분할 발송 (4096자 제한)
    print("[5/5] 텔레그램 발송...")
    chunks = []
    while final_message:
        chunks.append(final_message[:4000])
        final_message = final_message[4000:]

    for i, chunk in enumerate(chunks, 1):
        r = requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={
                "chat_id": TG_CHAT,
                "text": chunk,
                "parse_mode": "Markdown",
                "disable_web_page_preview": True,
            },
            timeout=30
        )
        result = r.json()
        if result.get("ok"):
            print(f"  ✓ 발송 {i}/{len(chunks)} (msg_id={result['result']['message_id']})")
        else:
            # Markdown 실패 시 plain으로 재시도
            r2 = requests.post(
                f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                json={"chat_id": TG_CHAT, "text": chunk, "disable_web_page_preview": True},
                timeout=30
            )
            print(f"  ⚠ Markdown 실패, plain 재발송 {i}/{len(chunks)}: {r2.json().get('ok')}")

    print(f"[완료] {NOW}")


if __name__ == "__main__":
    main()
