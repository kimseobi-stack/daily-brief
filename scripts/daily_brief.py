"""
Daily Brief v3
- 시세 + 펀더멘털 (EPS, PEG, 컨센목표가) Yahoo Finance 직접
- AI 해설 (Gemini + OpenRouter, 실패 시 Gemini fallback)
- EPS 기반 4가지 전략 스크리닝
- 텔레그램 6분할 가독성 발송
"""
import os
import re
import time
import requests
import yfinance as yf
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

KR_STOCKS = [
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
US_STOCKS = [
    ("AAPL", "애플"), ("NVDA", "엔비디아"), ("MSFT", "MS"),
    ("GOOGL", "구글"), ("META", "메타"), ("TSLA", "테슬라"),
    ("AMZN", "아마존"), ("AVGO", "브로드컴"), ("AMD", "AMD"),
    ("PLTR", "팔란티어"), ("TSM", "TSMC"), ("ASML", "ASML"),
    ("BRK-B", "버크셔"), ("JPM", "JPM"), ("V", "비자"),
    ("WMT", "월마트"), ("LLY", "릴리"), ("UNH", "유나이티드헬스"),
    ("XOM", "엑손모빌"), ("COST", "코스트코"),
]


# ============================================================
# 1. 시세 (Yahoo chart API - 빠름)
# ============================================================
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


def fetch_indices():
    return {
        "KOSPI": yf_price("^KS11"),
        "KOSDAQ": yf_price("^KQ11"),
        "SP500": yf_price("^GSPC"),
        "NASDAQ": yf_price("^IXIC"),
        "DOW": yf_price("^DJI"),
        "USDKRW": yf_price("KRW=X"),
        "US10Y": yf_price("^TNX"),
        "VIX": yf_price("^VIX"),
        "BTC": yf_price("BTC-USD"),
        "WTI": yf_price("CL=F"),
        "GOLD": yf_price("GC=F"),
    }


def fetch_prices(stocks):
    out = []
    for sym, name in stocks:
        d = yf_price(sym)
        if d.get("price"):
            d["sym"] = sym.split(".")[0]
            d["yf_sym"] = sym
            d["name"] = name
            out.append(d)
        time.sleep(0.05)
    return out


# ============================================================
# 2. 펀더멘털 (yfinance .info)
# ============================================================
def fetch_fundamentals(symbol):
    """EPS, PEG, Forward PE, EPS Growth, 목표가, 추천 등."""
    try:
        info = yf.Ticker(symbol).info
        return {
            "trailing_eps": info.get("trailingEps"),
            "forward_eps": info.get("forwardEps"),
            "trailing_pe": info.get("trailingPE"),
            "forward_pe": info.get("forwardPE"),
            "peg": info.get("pegRatio"),
            "eps_growth": info.get("earningsGrowth"),
            "eps_q_growth": info.get("earningsQuarterlyGrowth"),
            "rev_growth": info.get("revenueGrowth"),
            "current": info.get("currentPrice"),
            "target_mean": info.get("targetMeanPrice"),
            "target_high": info.get("targetHighPrice"),
            "recommend": info.get("recommendationKey"),
            "analysts": info.get("numberOfAnalystOpinions"),
            "market_cap": info.get("marketCap"),
        }
    except Exception:
        return {}


def enrich_with_fundamentals(stocks):
    """가격에 펀더멘털 추가."""
    for s in stocks:
        f = fetch_fundamentals(s["yf_sym"])
        s.update(f)
        # 상승여력
        if s.get("target_mean") and s.get("price"):
            s["upside"] = (s["target_mean"] / s["price"] - 1) * 100
        else:
            s["upside"] = None
        time.sleep(0.15)
    return stocks


# ============================================================
# 3. EPS 기반 스크리닝 4 전략
# ============================================================
def screen_target_upside(stocks, n=5):
    """애널리스트 컨센서스 목표가 대비 상승여력 + 충분한 애널리스트 수."""
    elig = [s for s in stocks if s.get("upside") and (s.get("analysts") or 0) >= 10]
    return sorted(elig, key=lambda x: x["upside"], reverse=True)[:n]


def screen_garp(stocks, n=5):
    """PEG 1.0 이하 + EPS 성장률 양수 (저평가 성장주)."""
    elig = [s for s in stocks if s.get("peg") and 0 < s["peg"] <= 1.0
            and (s.get("eps_growth") or 0) > 0]
    return sorted(elig, key=lambda x: x["peg"])[:n]


def screen_eps_growth(stocks, n=5):
    """분기 EPS 성장률 30%+ (강한 모멘텀)."""
    elig = [s for s in stocks if (s.get("eps_q_growth") or 0) >= 0.30]
    return sorted(elig, key=lambda x: x["eps_q_growth"], reverse=True)[:n]


def screen_strong_buy(stocks, n=5):
    """애널리스트 strong_buy + 상승여력 10%+."""
    elig = [s for s in stocks if s.get("recommend") in ("strong_buy", "buy")
            and (s.get("upside") or 0) >= 10]
    return sorted(elig, key=lambda x: x["upside"], reverse=True)[:n]


# ============================================================
# 4. RSS 뉴스
# ============================================================
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
    return headlines[:40]


# ============================================================
# 5. AI 호출 (rate limit 회피용 sleep 포함)
# ============================================================
def call_gemini(prompt, model="gemini-flash-latest", temperature=0.3):
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={GEMINI_KEY}"
        r = requests.post(url, json={
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"maxOutputTokens": 2000, "temperature": temperature}
        }, timeout=60)
        if r.status_code != 200:
            return f"[ERROR Gemini {model} HTTP {r.status_code}]"
        d = r.json()
        if "candidates" not in d or not d["candidates"]:
            return f"[ERROR Gemini {model} no candidates]"
        text = d["candidates"][0].get("content", {}).get("parts", [{}])[0].get("text", "")
        if len(text) < 100:
            return f"[ERROR Gemini {model} too short]"
        return text
    except Exception as e:
        return f"[ERROR Gemini {model}: {type(e).__name__}]"


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
            json={"model": model, "messages": [{"role": "user", "content": prompt}], "max_tokens": 2000},
            timeout=120,
        )
        if r.status_code != 200:
            return f"[ERROR OR {model} HTTP {r.status_code}]"
        d = r.json()
        if "choices" not in d or not d["choices"]:
            return f"[ERROR OR {model} no choices]"
        text = d["choices"][0].get("message", {}).get("content", "")
        if len(text) < 100:
            return f"[ERROR OR {model} too short]"
        return text
    except Exception as e:
        return f"[ERROR OR {model}: {type(e).__name__}]"


def call_ai_with_fallback(prompt, slot):
    if slot == "AI1":
        out = call_gemini(prompt, "gemini-2.0-flash", 0.3)
        if out.startswith("[ERROR"):
            time.sleep(2)
            out = call_gemini(prompt, "gemini-flash-latest", 0.3)
        return out
    if slot == "AI2":
        out = call_openrouter(prompt, "openai/gpt-oss-120b:free")
        if out.startswith("[ERROR"):
            time.sleep(2)
            out = call_gemini(prompt + "\n\n[보수적 관점]", "gemini-flash-latest", 0.5)
        return out
    if slot == "AI3":
        out = call_openrouter(prompt, "qwen/qwen3-next-80b-a3b-instruct:free")
        if out.startswith("[ERROR"):
            time.sleep(2)
            out = call_gemini(prompt + "\n\n[공격적 관점]", "gemini-flash-latest", 0.7)
        return out
    return "[ERROR unknown]"


# ============================================================
# 6. 텔레그램 발송 (가독성 분할)
# ============================================================
def tg_send(text):
    text = text[:4000]
    r = requests.post(
        f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
        json={"chat_id": TG_CHAT, "text": text, "disable_web_page_preview": True},
        timeout=30,
    )
    return r.json().get("ok")


# ============================================================
# 7. 포맷터
# ============================================================
def fmt_idx(name, d, suffix=""):
    p = d.get("price")
    c = d.get("change_pct", 0)
    if not p:
        return f"{name}: N/A"
    sign = "+" if (c or 0) >= 0 else ""
    return f"{name:<8} {p:>10,.2f}{suffix}  {sign}{c:.2f}%"


def fmt_kr(s):
    sign = "+" if s["change_pct"] >= 0 else ""
    return f"{s['name']:<8} ({s['sym']})  {s['price']:>9,.0f}원  {sign}{s['change_pct']:.2f}%"


def fmt_us(s):
    sign = "+" if s["change_pct"] >= 0 else ""
    return f"{s['name']:<8} ({s['sym']:<5})  ${s['price']:>8,.2f}  {sign}{s['change_pct']:.2f}%"


def fmt_screen(s, market="kr"):
    tgt = s.get("target_mean")
    up = s.get("upside")
    peg = s.get("peg")
    g = s.get("eps_q_growth")
    parts = []
    if tgt:
        if market == "kr":
            parts.append(f"목표가 {tgt:,.0f}원")
        else:
            parts.append(f"목표가 ${tgt:,.2f}")
    if up is not None:
        parts.append(f"여력 {up:+.1f}%")
    if peg:
        parts.append(f"PEG {peg:.2f}")
    if g is not None:
        parts.append(f"분기성장 {g*100:+.1f}%")
    if s.get("analysts"):
        parts.append(f"애널 {s['analysts']}")
    suffix = " | ".join(parts)
    if market == "kr":
        return f"• {s['name']}({s['sym']}) {s['price']:,.0f}원\n  {suffix}"
    else:
        return f"• {s['name']}({s['sym']}) ${s['price']:,.2f}\n  {suffix}"


# ============================================================
# 8. 메인
# ============================================================
def main():
    print(f"[{NOW}] Daily Brief v3 시작")

    print("[1/6] 거시 지표...")
    ind = fetch_indices()
    print(f"  KOSPI={ind['KOSPI']['price']}, SP500={ind['SP500']['price']}")

    print("[2/6] 한국 시세...")
    kr = fetch_prices(KR_STOCKS)
    print("[3/6] 미국 시세...")
    us = fetch_prices(US_STOCKS)
    print(f"  한국 {len(kr)}, 미국 {len(us)}")

    print("[4/6] 펀더멘털 수집 (yfinance, 시간 소요)...")
    kr = enrich_with_fundamentals(kr)
    us = enrich_with_fundamentals(us)

    print("[5/6] 뉴스...")
    news = fetch_news()

    print("[6/6] AI 분석 + 발송 시작...")

    # ----- Msg 1: 헤더 + 거시 -----
    msg1 = (
        f"🌅 Daily Brief\n"
        f"📅 {TODAY}  |  📡 {NOW}\n"
        f"━━━━━━━━━━━━━━━\n"
        f"📊 거시 지표 (Yahoo 실시간)\n\n"
        f"{fmt_idx('KOSPI', ind['KOSPI'])}\n"
        f"{fmt_idx('KOSDAQ', ind['KOSDAQ'])}\n"
        f"{fmt_idx('SP500', ind['SP500'])}\n"
        f"{fmt_idx('NASDAQ', ind['NASDAQ'])}\n"
        f"{fmt_idx('DOW', ind['DOW'])}\n"
        f"\n"
        f"USD/KRW {ind['USDKRW']['price']:,.2f}  ({ind['USDKRW']['change_pct']:+.2f}%)\n"
        f"US 10Y  {ind['US10Y']['price']:.3f}%  ({ind['US10Y']['change_pct']:+.2f}%)\n"
        f"VIX     {ind['VIX']['price']:,.2f}  ({ind['VIX']['change_pct']:+.2f}%)\n"
        f"WTI     ${ind['WTI']['price']:,.2f}  ({ind['WTI']['change_pct']:+.2f}%)\n"
        f"GOLD    ${ind['GOLD']['price']:,.2f}  ({ind['GOLD']['change_pct']:+.2f}%)\n"
        f"BTC     ${ind['BTC']['price']:,.0f}  ({ind['BTC']['change_pct']:+.2f}%)\n"
    )

    # ----- Msg 2: 한국 등락 -----
    kr_sorted = sorted(kr, key=lambda x: x.get("change_pct", 0), reverse=True)
    msg2 = "🇰🇷 한국 시장 (실시간)\n━━━━━━━━━━━━━━━\n📈 상승 TOP 10\n\n"
    for s in kr_sorted[:10]:
        msg2 += fmt_kr(s) + "\n"
    msg2 += "\n📉 하락 TOP 10\n\n"
    for s in kr_sorted[-10:][::-1]:
        msg2 += fmt_kr(s) + "\n"

    # ----- Msg 3: 미국 등락 -----
    us_sorted = sorted(us, key=lambda x: x.get("change_pct", 0), reverse=True)
    msg3 = "🇺🇸 미국 시장 (실시간)\n━━━━━━━━━━━━━━━\n📈 상승 TOP 10\n\n"
    for s in us_sorted[:10]:
        msg3 += fmt_us(s) + "\n"
    msg3 += "\n📉 하락 TOP 10\n\n"
    for s in us_sorted[-10:][::-1]:
        msg3 += fmt_us(s) + "\n"

    # ----- Msg 4: 한국 EPS 스크리닝 -----
    kr_msg = "🇰🇷 한국 EPS 스크리닝\n━━━━━━━━━━━━━━━\n\n"
    kr_msg += "🎯 목표가 상승여력 TOP 5\n(애널 컨센서스 기준)\n\n"
    for s in screen_target_upside(kr):
        kr_msg += fmt_screen(s, "kr") + "\n\n"
    kr_msg += "\n💎 GARP (저평가 성장)\nPEG ≤ 1.0 + EPS 성장 양수\n\n"
    for s in screen_garp(kr):
        kr_msg += fmt_screen(s, "kr") + "\n\n"
    kr_msg += "\n🚀 EPS 분기성장 30%+\n\n"
    for s in screen_eps_growth(kr):
        kr_msg += fmt_screen(s, "kr") + "\n\n"
    kr_msg += "\n⭐ 애널 Strong Buy + 여력 10%+\n\n"
    for s in screen_strong_buy(kr):
        kr_msg += fmt_screen(s, "kr") + "\n\n"

    # ----- Msg 5: 미국 EPS 스크리닝 -----
    us_msg = "🇺🇸 미국 EPS 스크리닝\n━━━━━━━━━━━━━━━\n\n"
    us_msg += "🎯 목표가 상승여력 TOP 5\n\n"
    for s in screen_target_upside(us):
        us_msg += fmt_screen(s, "us") + "\n\n"
    us_msg += "\n💎 GARP (PEG ≤ 1.0 + 성장)\n\n"
    for s in screen_garp(us):
        us_msg += fmt_screen(s, "us") + "\n\n"
    us_msg += "\n🚀 EPS 분기성장 30%+\n\n"
    for s in screen_eps_growth(us):
        us_msg += fmt_screen(s, "us") + "\n\n"
    us_msg += "\n⭐ 애널 Strong Buy + 여력 10%+\n\n"
    for s in screen_strong_buy(us):
        us_msg += fmt_screen(s, "us") + "\n\n"

    # ----- Msg 6: AI 합의 해설 -----
    base_prompt = (
        "한미 주식 데일리브리핑 해설만 작성. 숫자 추측 금지.\n"
        f"오늘 {TODAY}\n\n"
        f"[지수] KOSPI {ind['KOSPI']['price']:,.2f}({ind['KOSPI']['change_pct']:+.2f}%), "
        f"SP500 {ind['SP500']['price']:,.2f}({ind['SP500']['change_pct']:+.2f}%), "
        f"USD/KRW {ind['USDKRW']['price']:,.2f}\n\n"
        f"[한국 상위] " + ", ".join(f"{s['name']}({s['sym']}) {s['change_pct']:+.1f}%" for s in kr_sorted[:5]) + "\n"
        f"[한국 하위] " + ", ".join(f"{s['name']}({s['sym']}) {s['change_pct']:+.1f}%" for s in kr_sorted[-5:]) + "\n"
        f"[미국 상위] " + ", ".join(f"{s['name']}({s['sym']}) {s['change_pct']:+.1f}%" for s in us_sorted[:5]) + "\n"
        f"[미국 하위] " + ", ".join(f"{s['name']}({s['sym']}) {s['change_pct']:+.1f}%" for s in us_sorted[-5:]) + "\n\n"
        f"[뉴스] " + " | ".join(news[:20]) + "\n\n"
        "출력:\n"
        "1. 시장 흐름 3줄 (코스피/SP500/환율 디커플링 여부)\n"
        "2. 한국 강세 섹터 분석 (어떤 섹터가 왜 올랐나)\n"
        "3. 미국 강세 섹터 분석\n"
        "4. 리스크 3가지\n"
        "5. 체크포인트 3가지\n"
        "총 1200자 이내."
    )

    ai_results = []
    for slot in ["AI1", "AI2", "AI3"]:
        result = call_ai_with_fallback(base_prompt, slot)
        ai_results.append(result)
        print(f"  {slot}: {len(result)}자")
        time.sleep(2)  # rate limit 회피

    valid = sum(1 for r in ai_results if not r.startswith("[ERROR") and len(r) > 200)
    print(f"  유효 AI: {valid}/3")

    meta_prompt = (
        "3개 AI 답변을 교차검증. 2개 이상 합의 항목만 채택. "
        "단독 의견 폐기. 모르면 '확인 필요' 표기.\n\n"
        f"[AI1]\n{ai_results[0]}\n\n[AI2]\n{ai_results[1]}\n\n[AI3]\n{ai_results[2]}\n\n"
        "출력:\n"
        "📌 핵심 3줄\n\n"
        "📊 시장 흐름 (한 단락)\n\n"
        "🇰🇷 한국 강세 섹터\n\n"
        "🇺🇸 미국 강세 섹터\n\n"
        "⚠️ 리스크 3\n\n"
        "✅ 체크포인트 3\n\n"
        "1500자 이내."
    )
    final_ai = call_gemini(meta_prompt)
    if final_ai.startswith("[ERROR"):
        final_ai = next((r for r in ai_results if not r.startswith("[ERROR")), "AI 분석 실패")

    msg6 = (
        f"🤖 AI 합의 해설 (유효 {valid}/3)\n"
        f"━━━━━━━━━━━━━━━\n\n"
        f"{final_ai}\n\n"
        f"━━━━━━━━━━━━━━━\n"
        f"※ 시세/펀더멘털: Yahoo Finance 실시간\n"
        f"※ EPS 스크리닝: 공개 컨센서스 기반\n"
        f"※ AI 해설: Gemini + OpenRouter 교차검증\n"
        f"※ 공개정보 요약, 투자권유 아님\n"
        f"※ 모든 매매 결정 본인 책임"
    )

    # ----- 발송 -----
    print("발송 시작...")
    for i, m in enumerate([msg1, msg2, msg3, kr_msg, us_msg, msg6], 1):
        ok = tg_send(m)
        print(f"  Msg {i}/6: {'OK' if ok else 'FAIL'}")
        time.sleep(1)

    print(f"[완료] {NOW}")


if __name__ == "__main__":
    main()
