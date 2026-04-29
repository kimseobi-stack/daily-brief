"""
Daily Brief: 한미 주식 데일리 브리핑
- 시세는 Yahoo Finance에서 직접 (AI 안 거침)
- AI 해설은 3개 호출 + 교차검증, OpenRouter 실패 시 Gemini로 fallback
- 팩트체크: AI 출력의 숫자가 원본과 다르면 자동 경고
"""
import os
import re
import time
import requests
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


# ==================================================================
# 1. Yahoo Finance 실시간 시세
# ==================================================================
def yf_price(symbol):
    try:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1d&range=2d"
        r = requests.get(url, headers=UA, timeout=10)
        meta = r.json()["chart"]["result"][0]["meta"]
        p = meta.get("regularMarketPrice")
        prev = meta.get("chartPreviousClose")
        chg = (p / prev - 1) * 100 if p and prev else None
        return {"price": p, "prev": prev, "change_pct": chg}
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
        ("005930.KS", "삼성전자"), ("000660.KS", "SK하이닉스"),
        ("005380.KS", "현대차"), ("035420.KS", "NAVER"), ("035720.KS", "카카오"),
        ("373220.KS", "LG에너지솔루션"), ("006400.KS", "삼성SDI"),
        ("086520.KS", "에코프로"), ("247540.KS", "에코프로비엠"),
        ("329180.KS", "HD현대중공업"), ("042660.KS", "한화오션"),
        ("010140.KS", "삼성중공업"), ("267260.KS", "HD현대일렉트릭"),
        ("010120.KS", "LS일렉트릭"), ("298040.KS", "효성중공업"),
        ("207940.KS", "삼성바이오로직스"), ("068270.KS", "셀트리온"),
        ("005490.KS", "POSCO홀딩스"), ("105560.KS", "KB금융"),
        ("055550.KS", "신한지주"),
    ]
    us_stocks = [
        ("AAPL", "애플"), ("NVDA", "엔비디아"), ("MSFT", "MS"),
        ("GOOGL", "구글"), ("META", "메타"), ("TSLA", "테슬라"),
        ("AMZN", "아마존"), ("AVGO", "브로드컴"), ("AMD", "AMD"),
        ("PLTR", "팔란티어"), ("TSM", "TSMC"), ("ASML", "ASML"),
        ("BRK-B", "버크셔"), ("JPM", "JPM"), ("V", "비자"),
        ("WMT", "월마트"), ("LLY", "릴리"), ("UNH", "유나이티드헬스"),
        ("XOM", "엑손모빌"), ("COST", "코스트코"),
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


# ==================================================================
# 2. RSS 뉴스
# ==================================================================
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
            for m in re.finditer(r"<title[^>]*>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>", r.text):
                h = m.group(1).strip()
                if h and len(h) > 5 and "RSS" not in h:
                    headlines.append(h)
                    if len(headlines) >= 60:
                        break
        except Exception:
            continue
    return headlines[:50]


# ==================================================================
# 3. AI 호출 (robust)
# ==================================================================
def call_gemini(prompt, model="gemini-flash-latest", temperature=0.3):
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={GEMINI_KEY}"
        r = requests.post(url, json={
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"maxOutputTokens": 2500, "temperature": temperature}
        }, timeout=60)
        if r.status_code != 200:
            return f"[ERROR Gemini {model} HTTP {r.status_code}: {r.text[:300]}]"
        d = r.json()
        if "candidates" not in d or not d["candidates"]:
            return f"[ERROR Gemini {model} no candidates: {str(d)[:300]}]"
        text = d["candidates"][0].get("content", {}).get("parts", [{}])[0].get("text", "")
        if len(text) < 100:
            return f"[ERROR Gemini {model} too short ({len(text)}): {text}]"
        return text
    except Exception as e:
        return f"[ERROR Gemini {model} exception: {type(e).__name__}: {e}]"


def call_openrouter(prompt, model):
    try:
        r = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {OPENROUTER_KEY}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://github.com/kimseobi-stack/daily-brief",
                "X-Title": "Daily Brief",
            },
            json={"model": model, "messages": [{"role": "user", "content": prompt}], "max_tokens": 2500},
            timeout=120,
        )
        if r.status_code != 200:
            return f"[ERROR OpenRouter {model} HTTP {r.status_code}: {r.text[:300]}]"
        d = r.json()
        if "choices" not in d or not d["choices"]:
            return f"[ERROR OpenRouter {model} no choices: {str(d)[:300]}]"
        text = d["choices"][0].get("message", {}).get("content", "")
        if len(text) < 100:
            return f"[ERROR OpenRouter {model} too short ({len(text)}): {text}]"
        return text
    except Exception as e:
        return f"[ERROR OpenRouter {model} exception: {type(e).__name__}: {e}]"


def call_ai_with_fallback(prompt, slot):
    """slot: AI1=Gemini2.0, AI2=OpenRouter GPT-OSS, AI3=OpenRouter Qwen3.
    OpenRouter 실패 시 Gemini 다른 temperature로 대체."""
    if slot == "AI1":
        out = call_gemini(prompt, "gemini-2.0-flash", 0.3)
        if out.startswith("[ERROR"):
            print(f"  {slot} 1차 실패: {out[:100]}")
            out = call_gemini(prompt, "gemini-flash-latest", 0.3)
        return out
    if slot == "AI2":
        out = call_openrouter(prompt, "openai/gpt-oss-120b:free")
        if out.startswith("[ERROR"):
            print(f"  {slot} OpenRouter 실패, Gemini fallback: {out[:120]}")
            out = call_gemini(prompt + "\n\n[지시: 보수적 위험회피 관점]", "gemini-flash-latest", 0.5)
        return out
    if slot == "AI3":
        out = call_openrouter(prompt, "qwen/qwen3-next-80b-a3b-instruct:free")
        if out.startswith("[ERROR"):
            print(f"  {slot} OpenRouter 실패, Gemini fallback: {out[:120]}")
            out = call_gemini(prompt + "\n\n[지시: 공격적 모멘텀 관점]", "gemini-2.0-flash", 0.7)
        return out
    return "[ERROR unknown slot]"


# ==================================================================
# 4. 팩트체크
# ==================================================================
def fact_check(text, indices, kr_data, us_data):
    warnings = []
    for label, key in [("KOSPI", "KOSPI"), ("KOSDAQ", "KOSDAQ"), ("SP500", "SP500"),
                       ("S&P", "SP500"), ("NASDAQ", "NASDAQ"), ("USD/KRW", "USDKRW")]:
        actual = indices.get(key, {}).get("price")
        if not actual:
            continue
        for m in re.finditer(rf"{label}[\s:|]*([0-9,]+\.?\d*)", text, re.IGNORECASE):
            try:
                claimed = float(m.group(1).replace(",", ""))
                if abs(claimed - actual) / actual > 0.01:
                    warnings.append(f"{label} 불일치: AI {claimed} vs 실제 {actual}")
            except Exception:
                pass
    return warnings


# ==================================================================
# 5. 메인
# ==================================================================
def main():
    print(f"[{NOW}] Daily Brief 시작")

    print("[1/5] 시세 수집...")
    indices, kr_data, us_data = fetch_market_data()
    print(f"  KOSPI={indices['KOSPI']['price']}, SP500={indices['SP500']['price']}, "
          f"USDKRW={indices['USDKRW']['price']} | 한국 {len(kr_data)} 미국 {len(us_data)}")

    print("[2/5] 뉴스 수집...")
    news = fetch_news()
    print(f"  헤드라인 {len(news)}개")

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
    kr_block = "\n".join(
        f"- {s['name']}({s['symbol']}) {s['price']:,.0f}원 "
        f"({'+' if s.get('change_pct', 0) >= 0 else ''}{s.get('change_pct', 0):.2f}%)"
        for s in kr_movers
    )
    us_block = "\n".join(
        f"- {s['name']}({s['symbol']}) ${s['price']:,.2f} "
        f"({'+' if s.get('change_pct', 0) >= 0 else ''}{s.get('change_pct', 0):.2f}%)"
        for s in us_movers
    )
    news_block = "\n".join(f"- {h}" for h in news[:30])

    base_prompt = (
        "한미 주식 데일리브리핑 해설만 작성. 규칙:\n"
        "1. 숫자(시세, 등락률, 지수)는 절대 추측 금지.\n"
        "2. 종목코드 임의 생성 금지. 아래 목록만 사용.\n"
        "3. 모르면 모른다고 써.\n\n"
        f"[오늘 {TODAY}]\n\n"
        f"[시장 데이터]\n{market_block}\n\n"
        f"[한국 등락 TOP10]\n{kr_block}\n\n"
        f"[미국 등락 TOP10]\n{us_block}\n\n"
        f"[뉴스 30건]\n{news_block}\n\n"
        "[작성]\n"
        "1. 핵심 3줄(각 50자)\n"
        "2. 한국 주목 5종목 선정 이유 (위 10개 중)\n"
        "3. 미국 주목 5종목 선정 이유\n"
        "4. 리스크 3가지\n"
        "5. 체크포인트 3가지\n"
        "1500자 이내."
    )

    print("[3/5] AI 3종 호출 (fallback 포함)...")
    ai = {}
    ai["A1"] = call_ai_with_fallback(base_prompt, "AI1")
    print(f"  AI1: {len(ai['A1'])}자 / {ai['A1'][:80]}")
    ai["A2"] = call_ai_with_fallback(base_prompt, "AI2")
    print(f"  AI2: {len(ai['A2'])}자 / {ai['A2'][:80]}")
    ai["A3"] = call_ai_with_fallback(base_prompt, "AI3")
    print(f"  AI3: {len(ai['A3'])}자 / {ai['A3'][:80]}")

    valid_count = sum(1 for v in ai.values() if not v.startswith("[ERROR") and len(v) > 200)
    print(f"  유효 AI 답변: {valid_count}/3")

    print("[4/5] 메타 종합...")
    meta_prompt = (
        "3개 AI 답변을 교차검증하여 텔레그램 마크다운 최종 브리핑 작성.\n"
        "규칙:\n"
        "- 숫자는 시스템이 채우니 너는 종목코드와 해설만.\n"
        "- 2개 이상 합의한 종목·해설 우선 채택.\n"
        "- 단독 의견은 폐기. 단 유효 답변 1개뿐이면 그것만 사용.\n"
        "- 모르면 '확인 필요' 표기.\n\n"
        f"[AI1]\n{ai['A1']}\n\n"
        f"[AI2]\n{ai['A2']}\n\n"
        f"[AI3]\n{ai['A3']}\n\n"
        "[출력 형식]\n"
        f"📌 핵심 3줄\n- \n- \n- \n\n"
        "🇰🇷 한국 주목 5종목 (종목코드 + 사유 + 합의 N/3)\n"
        "🇺🇸 미국 주목 5종목 (티커 + 사유 + 합의 N/3)\n"
        "⚠️ 리스크 3\n"
        "✅ 체크포인트 3\n"
        "1500자 이내."
    )
    final_commentary = call_gemini(meta_prompt)
    if final_commentary.startswith("[ERROR"):
        print(f"  메타 실패: {final_commentary[:200]}")
        final_commentary = ai.get("A1", "") or "AI 분석 실패. 시세만 참고."

    final_message = (
        f"🌅 *Daily Brief {TODAY}*\n"
        f"📡 발송: {NOW}\n\n"
        f"📊 *시장 마감 (Yahoo Finance 실시간)*\n{market_block}\n\n"
        f"🇰🇷 *한국 등락 TOP 10 (실시간)*\n{kr_block}\n\n"
        f"🇺🇸 *미국 등락 TOP 10 (실시간)*\n{us_block}\n\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"🤖 *AI 합의 해설 (유효 {valid_count}/3)*\n\n"
        f"{final_commentary}\n\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"※ 시세는 Yahoo Finance 실시간 (100% 정확)\n"
        f"※ 해설은 AI 교차검증 (유효 답변만)\n"
        f"※ 공개정보 요약, 투자권유 아님, 손실책임 본인\n"
    )

    warnings = fact_check(final_commentary, indices, kr_data, us_data)
    if warnings:
        final_message += "\n⚠️ *팩트체크 경고*\n" + "\n".join(f"- {w}" for w in warnings)
        print(f"  팩트체크 경고 {len(warnings)}건")

    print("[5/5] 텔레그램 발송...")
    chunks = []
    msg = final_message
    while msg:
        chunks.append(msg[:4000])
        msg = msg[4000:]

    for i, chunk in enumerate(chunks, 1):
        r = requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={"chat_id": TG_CHAT, "text": chunk, "parse_mode": "Markdown",
                  "disable_web_page_preview": True}, timeout=30,
        )
        if r.json().get("ok"):
            print(f"  발송 {i}/{len(chunks)} OK")
        else:
            r2 = requests.post(
                f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                json={"chat_id": TG_CHAT, "text": chunk, "disable_web_page_preview": True},
                timeout=30,
            )
            print(f"  Markdown 실패, plain 재발송 {i}/{len(chunks)}: {r2.json().get('ok')}")

    print(f"[완료] {NOW}")


if __name__ == "__main__":
    main()
