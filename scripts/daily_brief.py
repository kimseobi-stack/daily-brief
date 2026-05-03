"""
Daily Brief v4 - 전문가 톤 + AI 매수/매도 시그널 + 추천 종목 + 통합 분석
"""
import os
import re
import time
import json as jsonlib
import requests
import yfinance as yf
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")
TODAY = datetime.now(KST).strftime("%Y-%m-%d")
NOW = datetime.now(KST).strftime("%Y-%m-%d %H:%M KST")
WEEKDAY = datetime.now(KST).strftime("%a")

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
# 1. 시세 + 펀더멘털
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
        return {"price": None, "change_pct": None}


def fetch_indices():
    return {k: yf_price(s) for k, s in [
        ("KOSPI", "^KS11"), ("KOSDAQ", "^KQ11"),
        ("SP500", "^GSPC"), ("NASDAQ", "^IXIC"), ("DOW", "^DJI"),
        ("USDKRW", "KRW=X"), ("US10Y", "^TNX"), ("VIX", "^VIX"),
        ("BTC", "BTC-USD"), ("WTI", "CL=F"), ("GOLD", "GC=F"),
    ]}


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


def fetch_fundamentals(stocks):
    """yfinance.info + recommendations로 펀더멘털 수집."""
    for s in stocks:
        try:
            t = yf.Ticker(s["yf_sym"])
            info = t.info
            s["forward_pe"] = info.get("forwardPE")
            s["peg"] = info.get("pegRatio")
            s["eps_growth"] = info.get("earningsGrowth")
            s["eps_q_growth"] = info.get("earningsQuarterlyGrowth")
            s["rev_growth"] = info.get("revenueGrowth")
            s["target_mean"] = info.get("targetMeanPrice")
            s["target_high"] = info.get("targetHighPrice")
            s["target_low"] = info.get("targetLowPrice")
            s["recommend"] = info.get("recommendationKey")
            s["rec_mean"] = info.get("recommendationMean")
            s["analysts"] = info.get("numberOfAnalystOpinions")
            s["beta"] = info.get("beta")
            s["52w_high"] = info.get("fiftyTwoWeekHigh")
            s["52w_low"] = info.get("fiftyTwoWeekLow")
            s["upside"] = (s["target_mean"] / s["price"] - 1) * 100 if s.get("target_mean") and s.get("price") else None
            s["off_high"] = (s["price"] / s["52w_high"] - 1) * 100 if s.get("52w_high") and s.get("price") else None

            # 추천 분포 (분기별 0m = 현재 월)
            try:
                rec_df = t.recommendations
                if rec_df is not None and not rec_df.empty:
                    cur = rec_df.iloc[0]
                    s["rec_dist"] = {
                        "strongBuy": int(cur.get("strongBuy", 0) or 0),
                        "buy": int(cur.get("buy", 0) or 0),
                        "hold": int(cur.get("hold", 0) or 0),
                        "sell": int(cur.get("sell", 0) or 0),
                        "strongSell": int(cur.get("strongSell", 0) or 0),
                    }
            except Exception:
                s["rec_dist"] = None
        except Exception:
            pass
        time.sleep(0.15)
    return stocks


def fmt_rec_dist(s):
    """애널 추천 분포 한 줄 포맷."""
    rd = s.get("rec_dist")
    if not rd:
        return f"애널 {s.get('analysts') or 0}명"
    total = sum(rd.values())
    if total == 0:
        return f"애널 {s.get('analysts') or 0}명"
    return (f"애널 {total}명: 강매{rd['strongBuy']} 매수{rd['buy']} "
            f"홀드{rd['hold']} 매도{rd['sell']} 강매도{rd['strongSell']}")


# ============================================================
# 2. 종합 점수 (AI 추천 + 펀더멘털 정량)
# ============================================================
def score_stock(s):
    """0~100 점수.
    구성: 목표가 여력(30) + EPS성장(20) + GARP(15) + 추천등급(15) + 모멘텀(10) + 애널수(10)
    """
    score = 0
    detail = []

    # 1. 목표가 상승여력 (30점)
    up = s.get("upside") or 0
    pts = min(max(up, 0) / 30 * 30, 30)  # 30% 여력 = 만점
    score += pts
    detail.append(f"여력 +{up:.1f}% ({pts:.0f}/30)")

    # 2. EPS 성장 (20점)
    g = (s.get("eps_q_growth") or 0) * 100
    pts = min(max(g, 0) / 50 * 20, 20)  # 50%+ = 만점
    score += pts
    detail.append(f"분기성장 {g:+.0f}% ({pts:.0f}/20)")

    # 3. GARP - PEG가 낮을수록 좋음 (15점)
    peg = s.get("peg")
    if peg and peg > 0:
        pts = min(max(2 - peg, 0) / 2 * 15, 15)  # PEG 0이면 만점, 2면 0
        detail.append(f"PEG {peg:.2f} ({pts:.0f}/15)")
    else:
        pts = 0
        detail.append(f"PEG N/A (0/15)")
    score += pts

    # 4. 애널 추천 등급 (15점)
    rec = s.get("recommend") or ""
    rec_score = {"strong_buy": 15, "buy": 12, "hold": 7, "sell": 3, "strong_sell": 0}.get(rec, 5)
    score += rec_score
    detail.append(f"추천 {rec or 'N/A'} ({rec_score}/15)")

    # 5. 모멘텀 (52주 고점 대비 -10% 이내면 강함) (10점)
    off = s.get("off_high")
    if off is not None:
        pts = max(10 + off, 0) if off > -10 else max(5 + off / 2, 0)
        pts = min(pts, 10)
    else:
        pts = 5
    score += pts
    detail.append(f"고점대비 {off:+.1f}% ({pts:.0f}/10)" if off is not None else "고점대비 N/A")

    # 6. 애널리스트 수 (커버리지) (10점)
    a = s.get("analysts") or 0
    pts = min(a / 30 * 10, 10)
    score += pts
    detail.append(f"애널 {a}명 ({pts:.0f}/10)")

    return round(score, 1), detail


def signal_emoji(score):
    """점수 → 직관 시그널."""
    if score >= 75:
        return "🔥 강력 매수"
    if score >= 60:
        return "🟢 매수"
    if score >= 45:
        return "🟡 홀드"
    if score >= 30:
        return "🟠 매도 검토"
    return "🔴 매도"


def calc_levels(s):
    """진입가 / 익절가 / 손절가 자동 계산.
    진입: 현재가
    익절: 목표가 또는 +15%
    손절: -7% 또는 52주 저점
    """
    price = s["price"]
    target = s.get("target_mean") or price * 1.15
    entry = price
    take_profit = min(target, price * 1.30)  # 목표가 또는 +30% 중 보수
    stop_loss = price * 0.93  # -7%
    return entry, take_profit, stop_loss


# ============================================================
# 3. RSS 뉴스
# ============================================================
def fetch_news():
    feeds = [
        "https://www.hankyung.com/feed/finance",
        "https://www.mk.co.kr/rss/50200011/",
        "https://www.yna.co.kr/rss/economy.xml",
        "https://biz.chosun.com/site/data/rss/rss.xml",
    ]
    out = []
    for url in feeds:
        try:
            r = requests.get(url, headers=UA, timeout=10)
            for m in re.finditer(r"<title[^>]*>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>", r.text):
                h = m.group(1).strip()
                if h and len(h) > 5 and "RSS" not in h:
                    out.append(h)
                    if len(out) >= 50:
                        break
        except Exception:
            continue
    return out[:40]


# ============================================================
# 4. AI 호출
# ============================================================
def call_gemini(prompt, model="gemini-flash-latest", temp=0.3):
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={GEMINI_KEY}"
        r = requests.post(url, json={
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"maxOutputTokens": 2000, "temperature": temp}
        }, timeout=60)
        if r.status_code != 200:
            return f"[ERROR Gemini {model} HTTP {r.status_code}]"
        d = r.json()
        text = d.get("candidates", [{}])[0].get("content", {}).get("parts", [{}])[0].get("text", "")
        return text if len(text) > 100 else f"[ERROR Gemini {model} short]"
    except Exception as e:
        return f"[ERROR Gemini {model}: {type(e).__name__}]"


def call_openrouter(prompt, model):
    try:
        r = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={"Authorization": f"Bearer {OPENROUTER_KEY}", "Content-Type": "application/json",
                     "HTTP-Referer": "https://github.com/kimseobi-stack/daily-brief", "X-Title": "Daily Brief"},
            json={"model": model, "messages": [{"role": "user", "content": prompt}], "max_tokens": 2000},
            timeout=120)
        if r.status_code != 200:
            return f"[ERROR OR {model} HTTP {r.status_code}]"
        d = r.json()
        text = d.get("choices", [{}])[0].get("message", {}).get("content", "")
        return text if len(text) > 100 else f"[ERROR OR {model} short]"
    except Exception as e:
        return f"[ERROR OR {model}: {type(e).__name__}]"


def call_ai(prompt, slot):
    if slot == 1:
        out = call_gemini(prompt, "gemini-2.0-flash", 0.3)
        if out.startswith("[ERROR"):
            time.sleep(2)
            out = call_gemini(prompt, "gemini-flash-latest", 0.3)
        return out
    if slot == 2:
        out = call_openrouter(prompt, "openai/gpt-oss-120b:free")
        if out.startswith("[ERROR"):
            time.sleep(2)
            out = call_gemini(prompt + "\n\n[보수적 분석]", "gemini-flash-latest", 0.5)
        return out
    if slot == 3:
        out = call_openrouter(prompt, "qwen/qwen3-next-80b-a3b-instruct:free")
        if out.startswith("[ERROR"):
            time.sleep(2)
            out = call_gemini(prompt + "\n\n[공격적 분석]", "gemini-flash-latest", 0.7)
        return out


# ============================================================
# 5. 텔레그램
# ============================================================
def tg_send(text):
    text = text[:4000]
    r = requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                      json={"chat_id": TG_CHAT, "text": text, "disable_web_page_preview": True}, timeout=30)
    return r.json().get("ok")


# ============================================================
# 6. 메인
# ============================================================
def main():
    print(f"[{NOW}] Daily Brief v4")

    print("[1/5] 시세/펀더멘털 수집...")
    ind = fetch_indices()
    kr = fetch_fundamentals(fetch_prices(KR_STOCKS))
    us = fetch_fundamentals(fetch_prices(US_STOCKS))
    print(f"  한국 {len(kr)}, 미국 {len(us)}")

    print("[2/5] 종목 점수 계산...")
    for s in kr + us:
        s["score"], s["detail"] = score_stock(s)
        s["signal"] = signal_emoji(s["score"])

    kr_top = sorted(kr, key=lambda x: x["score"], reverse=True)[:5]
    us_top = sorted(us, key=lambda x: x["score"], reverse=True)[:5]

    print("[3/5] 뉴스 수집...")
    news = fetch_news()

    print("[4/5] AI 분석...")
    base_prompt = (
        f"한미 주식시장 전문가 분석 ({TODAY}).\n\n"
        f"[지수] KOSPI {ind['KOSPI']['price']:,.2f}({ind['KOSPI']['change_pct']:+.2f}%), "
        f"SP500 {ind['SP500']['price']:,.2f}({ind['SP500']['change_pct']:+.2f}%), "
        f"USD/KRW {ind['USDKRW']['price']:,.2f}, VIX {ind['VIX']['price']:.2f}\n\n"
        f"[한국 점수 TOP 5]\n" + "\n".join(
            f"{s['name']}({s['sym']}) 점수{s['score']} | 여력{s.get('upside') or 0:+.0f}% | EPS{(s.get('eps_q_growth') or 0)*100:+.0f}% | PEG{s.get('peg') or 0:.2f}"
            for s in kr_top) + "\n\n"
        f"[미국 점수 TOP 5]\n" + "\n".join(
            f"{s['name']}({s['sym']}) 점수{s['score']} | 여력{s.get('upside') or 0:+.0f}% | EPS{(s.get('eps_q_growth') or 0)*100:+.0f}% | PEG{s.get('peg') or 0:.2f}"
            for s in us_top) + "\n\n"
        f"[뉴스 헤드라인]\n" + "\n".join(news[:20]) + "\n\n"
        "다음 형식 그대로 출력 (1500자 이내):\n\n"
        "📊 시장 진단\n(코스피/SP500 흐름, 환율, 변동성 한 단락)\n\n"
        "🎯 오늘의 핵심 매수 후보 (한국)\n"
        "1순위: 종목명(코드) - 매수 근거 1줄\n"
        "2순위: ...\n\n"
        "🎯 오늘의 핵심 매수 후보 (미국)\n"
        "1순위: ...\n2순위: ...\n\n"
        "⚠️ 단기 회피 종목 (있으면)\n\n"
        "🔮 향후 일주일 전망 한 단락"
    )

    ai_results = []
    for slot in [1, 2, 3]:
        r = call_ai(base_prompt, slot)
        ai_results.append(r)
        print(f"  AI{slot}: {len(r)}자")
        time.sleep(2)

    valid = sum(1 for r in ai_results if not r.startswith("[ERROR") and len(r) > 200)
    meta_prompt = (
        "3개 AI 답변을 종합. 2개 이상 합의 항목만 채택. 중복 제거. 직설적 전문가 톤.\n\n"
        f"[AI1]\n{ai_results[0]}\n\n[AI2]\n{ai_results[1]}\n\n[AI3]\n{ai_results[2]}\n\n"
        "출력 형식 (1500자):\n"
        "📊 시장 진단\n\n"
        "🎯 한국 핵심 매수 (3개)\n1. 종목(코드) - 근거\n\n"
        "🎯 미국 핵심 매수 (3개)\n\n"
        "⚠️ 회피 종목\n\n"
        "🔮 일주일 전망"
    )
    final = call_gemini(meta_prompt)
    if final.startswith("[ERROR"):
        final = next((r for r in ai_results if not r.startswith("[ERROR")), "분석 실패")

    # ============ 메시지 작성 ============
    print("[5/5] 발송...")

    # Msg 1: 거시
    msg1 = (
        f"🌅 Daily Brief\n📅 {TODAY} ({WEEKDAY})  📡 {NOW}\n"
        f"━━━━━━━━━━━━━━━\n📊 거시 지표\n\n"
        f"KOSPI    {ind['KOSPI']['price']:>10,.2f}  {ind['KOSPI']['change_pct']:+.2f}%\n"
        f"KOSDAQ   {ind['KOSDAQ']['price']:>10,.2f}  {ind['KOSDAQ']['change_pct']:+.2f}%\n"
        f"SP500    {ind['SP500']['price']:>10,.2f}  {ind['SP500']['change_pct']:+.2f}%\n"
        f"NASDAQ   {ind['NASDAQ']['price']:>10,.2f}  {ind['NASDAQ']['change_pct']:+.2f}%\n"
        f"DOW      {ind['DOW']['price']:>10,.2f}  {ind['DOW']['change_pct']:+.2f}%\n\n"
        f"USD/KRW  {ind['USDKRW']['price']:>10,.2f}  {ind['USDKRW']['change_pct']:+.2f}%\n"
        f"US 10Y   {ind['US10Y']['price']:>10.3f}%  {ind['US10Y']['change_pct']:+.2f}%\n"
        f"VIX      {ind['VIX']['price']:>10,.2f}  {ind['VIX']['change_pct']:+.2f}%\n"
        f"WTI     ${ind['WTI']['price']:>9,.2f}  {ind['WTI']['change_pct']:+.2f}%\n"
        f"GOLD    ${ind['GOLD']['price']:>9,.2f}  {ind['GOLD']['change_pct']:+.2f}%\n"
        f"BTC     ${ind['BTC']['price']:>9,.0f}  {ind['BTC']['change_pct']:+.2f}%\n"
    )

    # Msg 2: 한국 추천 TOP 5 (점수 기반)
    msg2 = "🇰🇷 한국 매수 후보 TOP 5\n━━━━━━━━━━━━━━━\n(AI + 펀더멘털 종합 점수)\n\n"
    for i, s in enumerate(kr_top, 1):
        e, tp, sl = calc_levels(s)
        msg2 += (
            f"{i}. {s['signal']}  점수 {s['score']}/100\n"
            f"   {s['name']} ({s['sym']})\n"
            f"   현재 {s['price']:,.0f}원"
        )
        if s.get("target_mean"):
            msg2 += f" → 목표 {s['target_mean']:,.0f}원 ({s.get('upside') or 0:+.1f}%)"
        msg2 += "\n"
        msg2 += f"   진입 {e:,.0f} / 익절 {tp:,.0f} / 손절 {sl:,.0f}\n"
        msg2 += f"   👥 {fmt_rec_dist(s)}\n"
        msg2 += f"   {' | '.join(s['detail'][:4])}\n\n"

    # Msg 3: 미국 추천 TOP 5
    msg3 = "🇺🇸 미국 매수 후보 TOP 5\n━━━━━━━━━━━━━━━\n(AI + 펀더멘털 종합 점수)\n\n"
    for i, s in enumerate(us_top, 1):
        e, tp, sl = calc_levels(s)
        msg3 += (
            f"{i}. {s['signal']}  점수 {s['score']}/100\n"
            f"   {s['name']} ({s['sym']})\n"
            f"   현재 ${s['price']:,.2f}"
        )
        if s.get("target_mean"):
            msg3 += f" → 목표 ${s['target_mean']:,.2f} ({s.get('upside') or 0:+.1f}%)"
        msg3 += "\n"
        msg3 += f"   진입 ${e:,.2f} / 익절 ${tp:,.2f} / 손절 ${sl:,.2f}\n"
        msg3 += f"   👥 {fmt_rec_dist(s)}\n"
        msg3 += f"   {' | '.join(s['detail'][:4])}\n\n"

    # Msg 4: 한국 등락 TOP/BOTTOM
    kr_sorted = sorted(kr, key=lambda x: x.get("change_pct", 0), reverse=True)
    msg4 = "🇰🇷 한국 시장 등락\n━━━━━━━━━━━━━━━\n📈 상승 TOP 8\n\n"
    for s in kr_sorted[:8]:
        msg4 += f"{s['name']:<8}({s['sym']}) {s['price']:>9,.0f}원 {s['change_pct']:+6.2f}% {s['signal'].split()[0]}\n"
    msg4 += "\n📉 하락 TOP 8\n\n"
    for s in kr_sorted[-8:][::-1]:
        msg4 += f"{s['name']:<8}({s['sym']}) {s['price']:>9,.0f}원 {s['change_pct']:+6.2f}% {s['signal'].split()[0]}\n"

    