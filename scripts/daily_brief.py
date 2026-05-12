"""Daily Brief v8 - 키움 모의/실전 연동 + 30주선 룰 + 매일 2회 (KST 7시/17시)"""
import os, re, time, json as jsonlib, requests
import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime
from zoneinfo import ZoneInfo

try:
    import kiwoom_sync as kw
    KIWOOM_AVAILABLE = True
except ImportError:
    KIWOOM_AVAILABLE = False

KST = ZoneInfo("Asia/Seoul")
TODAY = datetime.now(KST).strftime("%Y-%m-%d")
NOW = datetime.now(KST).strftime("%Y-%m-%d %H:%M KST")
HOUR = datetime.now(KST).hour
SESSION = "오전 7시 (해외장 마감 후)" if HOUR < 12 else "오후 5시 (한국장 마감 후)"

GEMINI_KEY = os.environ["GEMINI_API_KEY"]
OPENROUTER_KEY = os.environ["OPENROUTER_API_KEY"]
TG_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TG_CHAT = os.environ["TELEGRAM_CHAT_ID"]
HOLDINGS = jsonlib.loads(os.environ.get("HOLDINGS_JSON", "[]"))

UA = {"User-Agent": "Mozilla/5.0"}

KR_STOCKS = [
    ("005930.KS", "삼성전자"), ("000660.KS", "SK하이닉스"),
    ("005380.KS", "현대차"), ("035420.KS", "NAVER"), ("035720.KS", "카카오"),
    ("373220.KS", "LG에너지"), ("006400.KS", "삼성SDI"),
    ("086520.KS", "에코프로"), ("247540.KS", "에코프로비엠"),
    ("329180.KS", "HD현대중공업"), ("042660.KS", "한화오션"),
    ("010140.KS", "삼성중공업"), ("267260.KS", "HD현대일렉트릭"),
    ("010120.KS", "LS일렉트릭"), ("298040.KS", "효성중공업"),
    ("207940.KS", "삼성바이오"), ("068270.KS", "셀트리온"),
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


# 키움 토큰 (모듈 로드 시 1회)
KIWOOM_TOKEN = kw.get_token() if KIWOOM_AVAILABLE else None
print(f"  Kiwoom Token: {'OK' if KIWOOM_TOKEN else 'NONE'}")


def yf_price_safe(symbol):
    """chart API + download fallback (에코프로 같은 캐시 오류 방지). 한국 종목은 키움 우선."""
    # 키움 우선 (한국 종목, 6자리 숫자)
    if KIWOOM_TOKEN and symbol.endswith(".KS"):
        kr_sym = symbol.replace(".KS", "")
        q = kw.fetch_kr_quote(KIWOOM_TOKEN, kr_sym)
        if q and q.get("price"):
            return {"price": q["price"], "prev": q["prev"], "change_pct": q["change_pct"]}
    try:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1d&range=5d"
        r = requests.get(url, headers=UA, timeout=10)
        meta = r.json()["chart"]["result"][0]["meta"]
        p = meta.get("regularMarketPrice")
        prev = meta.get("chartPreviousClose")

        # 검증: download의 마지막 종가와 5% 이상 차이 시 download 사용
        try:
            df = yf.download(symbol, period="5d", interval="1d",
                             progress=False, auto_adjust=False, threads=False)
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = [c[0] for c in df.columns]
            if not df.empty:
                dl_last = float(df["Close"].iloc[-1])
                dl_prev = float(df["Close"].iloc[-2]) if len(df) >= 2 else prev
                if p and abs(p - dl_last) / dl_last > 0.05:
                    p = dl_last
                    prev = dl_prev
        except Exception:
            pass
        chg = (p / prev - 1) * 100 if p and prev else None
        return {"price": p, "prev": prev, "change_pct": chg}
    except Exception:
        return {"price": None, "change_pct": None}


def fetch_indices():
    return {k: yf_price_safe(s) for k, s in [
        ("KOSPI", "^KS11"), ("SP500", "^GSPC"), ("NASDAQ", "^IXIC"),
        ("USDKRW", "KRW=X"), ("US10Y", "^TNX"), ("VIX", "^VIX"),
        ("WTI", "CL=F"),
    ]}


def fetch_prices(stocks):
    out = []
    for sym, name in stocks:
        d = yf_price_safe(sym)
        if d.get("price"):
            d["sym"] = sym.split(".")[0]
            d["yf_sym"] = sym
            d["name"] = name
            out.append(d)
        time.sleep(0.05)
    return out


def fetch_fundamentals(stocks):
    for s in stocks:
        try:
            t = yf.Ticker(s["yf_sym"])
            info = t.info
            s["peg"] = info.get("pegRatio")
            s["eps_q_growth"] = info.get("earningsQuarterlyGrowth")
            s["target_mean"] = info.get("targetMeanPrice")
            s["recommend"] = info.get("recommendationKey")
            s["analysts"] = info.get("numberOfAnalystOpinions")
            s["52w_high"] = info.get("fiftyTwoWeekHigh")
            s["upside"] = (s["target_mean"] / s["price"] - 1) * 100 if s.get("target_mean") and s.get("price") else None
            s["off_high"] = (s["price"] / s["52w_high"] - 1) * 100 if s.get("52w_high") and s.get("price") else None
            try:
                rec_df = t.recommendations
                if rec_df is not None and not rec_df.empty:
                    cur = rec_df.iloc[0]
                    s["rec_dist"] = {
                        "sb": int(cur.get("strongBuy", 0) or 0),
                        "b": int(cur.get("buy", 0) or 0),
                        "h": int(cur.get("hold", 0) or 0),
                        "s": int(cur.get("sell", 0) or 0),
                        "ss": int(cur.get("strongSell", 0) or 0),
                    }
            except Exception:
                s["rec_dist"] = None
        except Exception:
            pass
        time.sleep(0.15)
    return stocks


def weekly_ma_analysis(symbol):
    """주봉 5/10/20/30주선 + 활성 이평선 + 30주선 룰."""
    try:
        df = yf.download(symbol, period="2y", interval="1wk",
                         progress=False, auto_adjust=False, threads=False)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = [c[0] for c in df.columns]
        if df.empty or len(df) < 30:
            return None
        c = df["Close"]
        h = df["High"]
        l = df["Low"]
        o = df["Open"]
        ma5 = c.rolling(5).mean().iloc[-1]
        ma10 = c.rolling(10).mean().iloc[-1]
        ma20 = c.rolling(20).mean().iloc[-1]
        ma30 = c.rolling(30).mean().iloc[-1]
        cur = c.iloc[-1]
        # 30주선 완전 이탈 (OHLC 4값 모두 30주선 아래)
        full_break = max(o.iloc[-1], h.iloc[-1], l.iloc[-1], c.iloc[-1]) < ma30
        # 활성 이평선
        if cur > ma5: active = "5주선"
        elif cur > ma10: active = "10주선"
        elif cur > ma20: active = "20주선"
        elif cur > ma30: active = "30주선"
        else: active = "30주선이탈"
        return {
            "ma5": ma5, "ma10": ma10, "ma20": ma20, "ma30": ma30,
            "active": active, "full_break_30": full_break,
            "above_30": cur > ma30,
        }
    except Exception:
        return None


def holding_action_v7(s, w):
    """30주선 룰 우선 + 점수."""
    if w and w.get("full_break_30"):
        return "🔴 풀매도 (30주선 완전 이탈)"
    if w and not w.get("above_30"):
        return "🔴 30주선 이탈 — 매도 검토"
    sc = s.get("score", 50)
    chg = s.get("change_pct", 0) or 0
    if sc >= 70 and chg > 0:
        return "🔥 추가 매수"
    if sc >= 55 or (w and w.get("active") in ["5주선", "10주선"]):
        return "🟢 보유 유지"
    if sc >= 40:
        return "🟡 홀드 (관망)"
    return "🟠 일부 차익실현"


def score_stock(s):
    score = 0
    up = s.get("upside") or 0
    score += min(max(up, 0) / 30 * 25, 25)
    g = (s.get("eps_q_growth") or 0) * 100
    score += min(max(g, 0) / 50 * 20, 20)
    peg = s.get("peg")
    if peg and peg > 0:
        score += min(max(2 - peg, 0) / 2 * 10, 10)
    rec = s.get("recommend") or ""
    score += {"strong_buy": 10, "buy": 8, "hold": 4, "sell": 1, "strong_sell": 0}.get(rec, 4)
    chg = s.get("change_pct") or 0
    if chg > 0: score += min(chg / 5 * 15, 15)
    elif chg > -2: score += 5
    cs = s.get("chart_score", 50)
    score += (cs - 50) / 50 * 10 + 5
    a = s.get("analysts") or 0
    score += min(a / 30 * 10, 10)
    off = s.get("off_high")
    if off is not None:
        score += min(max(10 + off, 0), 5)
    return round(max(0, min(100, score)), 1)


def signal_emoji(score):
    if score >= 75: return "🔥"
    if score >= 60: return "🟢"
    if score >= 45: return "🟡"
    if score >= 30: return "🟠"
    return "🔴"


def detect_accumulation(ind):
    wti = (ind.get("WTI") or {}).get("change_pct") or 0
    us10y = (ind.get("US10Y") or {}).get("change_pct") or 0
    sp500 = (ind.get("SP500") or {}).get("change_pct") or 0
    vix = (ind.get("VIX") or {}).get("change_pct") or 0
    sigs = []
    if wti >= 1.0 and abs(us10y) < 0.5 and sp500 >= 0.5:
        sigs.append("🔥 매집 신호 (유가↑·금리잠잠·주가↑)")
    if us10y >= 1.0 and sp500 >= 0.5:
        sigs.append("📈 위험선호 (금리↑·주가↑)")
    if vix <= -3 and sp500 >= 1.0:
        sigs.append("😌 공포 해소")
    if vix >= 5 and sp500 <= -1:
        sigs.append("⚠️ 위험회피")
    return sigs


def fetch_news():
    feeds = ["https://www.hankyung.com/feed/finance",
             "https://www.mk.co.kr/rss/50200011/",
             "https://www.yna.co.kr/rss/economy.xml"]
    out = []
    for url in feeds:
        try:
            r = requests.get(url, headers=UA, timeout=10)
            for m in re.finditer(r"<title[^>]*>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>", r.text):
                h = m.group(1).strip()
                if h and len(h) > 5 and "RSS" not in h:
                    out.append(h)
                    if len(out) >= 30: break
        except Exception:
            continue
    return out[:25]


def call_gemini(prompt, model="gemini-flash-latest"):
    try:
        r = requests.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={GEMINI_KEY}",
            json={"contents": [{"parts": [{"text": prompt}]}],
                  "generationConfig": {"maxOutputTokens": 1500, "temperature": 0.3}},
            timeout=60)
        if r.status_code != 200: return ""
        d = r.json()
        return d.get("candidates", [{}])[0].get("content", {}).get("parts", [{}])[0].get("text", "")
    except Exception:
        return ""


def call_openrouter(prompt, model):
    try:
        r = requests.post("https://openrouter.ai/api/v1/chat/completions",
            headers={"Authorization": f"Bearer {OPENROUTER_KEY}", "Content-Type": "application/json",
                     "HTTP-Referer": "https://github.com/kimseobi-stack/daily-brief", "X-Title": "Daily Brief"},
            json={"model": model, "messages": [{"role": "user", "content": prompt}], "max_tokens": 1500},
            timeout=120)
        if r.status_code != 200: return ""
        return r.json().get("choices", [{}])[0].get("message", {}).get("content", "")
    except Exception:
        return ""


def best_ai(prompt):
    """3 AI 호출 후 가장 긴 답변 채택."""
    results = []
    r1 = call_gemini(prompt, "gemini-flash-latest")
    if len(r1) > 200: results.append(r1)
    time.sleep(2)
    r2 = call_openrouter(prompt, "openai/gpt-oss-120b:free")
    if len(r2) > 200: results.append(r2)
    time.sleep(2)
    r3 = call_openrouter(prompt, "meta-llama/llama-3.3-70b-instruct:free")
    if len(r3) > 200: results.append(r3)
    return max(results, key=len) if results else "분석 실패"


def tg_send(text):
    text = text[:4000]
    r = requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
        json={"chat_id": TG_CHAT, "text": text, "disable_web_page_preview": True}, timeout=30)
    return r.json().get("ok")


def main():
    print(f"[{NOW}] Daily Brief v8 - {SESSION}")
    ind = fetch_indices()
    kr = fetch_fundamentals(fetch_prices(KR_STOCKS))
    us = fetch_fundamentals(fetch_prices(US_STOCKS))

    # 보유 종목 (키움 + HOLDINGS_JSON 병합)
    holdings_stocks = []
    kw_balance = None
    kw_deposit = 0
    if KIWOOM_TOKEN:
        kw_balance = kw.fetch_balance(KIWOOM_TOKEN)
        if kw_balance:
            kw_deposit = kw_balance.get("deposit", 0)
            for h in kw_balance.get("holdings", []):
                if not h.get("sym"):
                    continue
                ks_sym = f"{h['sym']}.KS"
                holdings_stocks.append({
                    "sym": ks_sym, "yf_sym": ks_sym,
                    "name": h["name"], "qty": h["qty"],
                    "avg_price": h["avg_price"],
                    "price": h["cur_price"],
                    "prev": None, "change_pct": None,
                    "eval_amt": h["eval_amt"], "pl_amt": h["pl_amt"],
                    "kw_pl_pct": h["pl_pct"],
                    "_from_kiwoom": True,
                })
            kw_kr_syms = {h["sym"] for h in kw_balance.get("holdings", [])}
        else:
            kw_kr_syms = set()
    else:
        kw_kr_syms = set()

    # HOLDINGS_JSON 미국 종목 + 키움 미수록 한국 종목
    for h in HOLDINGS:
        sym_clean = h["sym"].replace(".KS", "")
        if sym_clean in kw_kr_syms:
            continue  # 키움에서 이미 가져옴
        d = yf_price_safe(h["sym"])
        if d.get("price"):
            d["sym"] = h["sym"]; d["yf_sym"] = h["sym"]
            d["name"] = h["name"]; d["qty"] = h.get("qty", 0)
            d["avg_price"] = h.get("avg_price")
            holdings_stocks.append(d)
        time.sleep(0.05)
    holdings_stocks = fetch_fundamentals(holdings_stocks)

    # 주봉 분석 (보유만)
    for s in holdings_stocks:
        s["weekly"] = weekly_ma_analysis(s["sym"])
        time.sleep(0.1)

    # 점수
    for s in kr + us + holdings_stocks:
        s["chart_score"] = 50
        s["score"] = score_stock(s)
        s["sig"] = signal_emoji(s["score"])

    for s in holdings_stocks:
        s["action"] = holding_action_v7(s, s.get("weekly"))

    # 30주선 룰: 신규 매수 후보 선정 전 필터링
    kr_candidates = sorted(kr, key=lambda x: x["score"], reverse=True)[:10]
    us_candidates = sorted(us, key=lambda x: x["score"], reverse=True)[:10]
    print("  주봉 30주선 체크 (후보 20개)...")
    for s in kr_candidates + us_candidates:
        s["weekly"] = weekly_ma_analysis(s["sym"])
        time.sleep(0.1)
    # 30주선 완전 이탈 또는 30주선 아래 = 매수 후보 자격 박탈
    kr_eligible = [s for s in kr_candidates
                   if s.get("weekly") and not s["weekly"].get("full_break_30") and s["weekly"].get("above_30")]
    us_eligible = [s for s in us_candidates
                   if s.get("weekly") and not s["weekly"].get("full_break_30") and s["weekly"].get("above_30")]
    kr_excluded = [s for s in kr_candidates if s not in kr_eligible][:3]
    us_excluded = [s for s in us_candidates if s not in us_eligible][:3]
    kr_top = kr_eligible[:3]
    us_top = us_eligible[:3]

    accum = detect_accumulation(ind)
    news = fetch_news()

    # AI 분석 (단일 통합)
    ai_prompt = (
        f"한미 주식 팩트 분석 ({TODAY} {SESSION}). 추측 금지, 사실만.\n"
        f"KOSPI {ind['KOSPI']['price']:.2f}({ind['KOSPI']['change_pct']:+.2f}%), "
        f"SP500 {ind['SP500']['price']:.2f}({ind['SP500']['change_pct']:+.2f}%), "
        f"USD/KRW {ind['USDKRW']['price']:.2f}, VIX {ind['VIX']['price']:.2f}\n"
        f"매집신호: {' / '.join(accum) if accum else '없음'}\n"
        f"한국TOP3: " + ", ".join(f"{s['name']}({s['sym']}) 점수{s['score']}" for s in kr_top) + "\n"
        f"미국TOP3: " + ", ".join(f"{s['name']}({s['sym']}) 점수{s['score']}" for s in us_top) + "\n"
        f"뉴스: " + " | ".join(news[:10]) + "\n\n"
        "출력 (700자 이내):\n"
        "📊 시장 한 줄\n"
        "🇰🇷 한국 주목 종목 1개 + 이유 1줄\n"
        "🇺🇸 미국 주목 종목 1개 + 이유 1줄\n"
        "⚠️ 오늘 리스크 1줄\n"
        "🔮 다음 흐름 1줄"
    )
    ai_text = best_ai(ai_prompt)
    print(f"  AI 답변: {len(ai_text)}자")

    # ===== Msg 1: 핵심 요약 =====
    msg1 = (
        f"🌅 Daily Brief — {SESSION}\n📅 {TODAY}  📡 {NOW}\n"
        f"━━━━━━━━━━━━━\n📊 거시 (실시간)\n\n"
        f"KOSPI    {ind['KOSPI']['price']:>10,.2f}  {ind['KOSPI']['change_pct']:+.2f}%\n"
        f"SP500    {ind['SP500']['price']:>10,.2f}  {ind['SP500']['change_pct']:+.2f}%\n"
        f"NASDAQ   {ind['NASDAQ']['price']:>10,.2f}  {ind['NASDAQ']['change_pct']:+.2f}%\n"
        f"USDKRW   {ind['USDKRW']['price']:>10,.2f}  {ind['USDKRW']['change_pct']:+.2f}%\n"
        f"US10Y    {ind['US10Y']['price']:>10.3f}%  {ind['US10Y']['change_pct']:+.2f}%\n"
        f"VIX      {ind['VIX']['price']:>10,.2f}  {ind['VIX']['change_pct']:+.2f}%\n"
        f"WTI     ${ind['WTI']['price']:>9,.2f}  {ind['WTI']['change_pct']:+.2f}%\n\n"
    )
    if accum:
        msg1 += "🌐 매집 신호\n" + "\n".join(accum) + "\n"
    else:
        msg1 += "🌐 매집 신호: 없음\n"

    # 키움 계좌 요약 (있으면 추가)
    if kw_balance:
        kw_total = kw_balance.get("tot_eval", 0)
        kw_pl = kw_balance.get("tot_pl", 0)
        kw_pl_pct = kw_balance.get("tot_pl_pct", 0)
        env_label = "모의" if os.environ.get("KIWOOM_BASE", "mock") == "mock" else "실전"
        msg1 += f"\n💰 키움 계좌 ({env_label})\n"
        msg1 += f"예수금  {kw_deposit:>14,}원\n"
        msg1 += f"평가금  {kw_total:>14,}원\n"
        msg1 += f"평가손익 {kw_pl:>+13,}원 ({kw_pl_pct:+.2f}%)\n"

    # ===== Msg 2: 보유 종목 (간결) =====
    msg2 = "💼 보유 종목 진단\n━━━━━━━━━━━━━\n"
    if not holdings_stocks:
        msg2 += "\n보유 종목 없음 (키움 모의 계좌 + HOLDINGS_JSON 모두 빈 상태)\n"
    for s in sorted(holdings_stocks, key=lambda x: 0 if "풀매도" in x.get("action","") else 1):
        cur = f"${s['price']:,.2f}" if not s["sym"].endswith(".KS") else f"{s['price']:,.0f}원"
        chg = s.get("change_pct") or 0
        w = s.get("weekly") or {}
        src = " 🟢키움" if s.get("_from_kiwoom") else ""
        msg2 += f"\n{s['action']}{src}\n"
        msg2 += f"{s['name']} ({s['sym'].replace('.KS','')}) {s['qty']}주\n"
        msg2 += f"가격 {cur} ({chg:+.2f}%) | 점수 {s['score']:.0f}/100\n"
        if s.get("avg_price"):
            avg = s["avg_price"]
            avg_str = f"${avg:,.2f}" if not s["sym"].endswith(".KS") else f"{avg:,.0f}원"
            msg2 += f"평균단가 {avg_str}"
            if s.get("kw_pl_pct") is not None:
                msg2 += f" | 손익 {s['kw_pl_pct']:+.2f}%"
            msg2 += "\n"
        if w:
            msg2 += f"주봉 활성: {w['active']} | 30주선 {'위' if w['above_30'] else '아래'}\n"
        if s.get("upside") is not None:
            msg2 += f"애널 목표가 여력 {s['upside']:+.0f}%\n"

    # ===== Msg 3: 신규 매수 후보 (30주선 통과만) =====
    msg3 = "🎯 신규 매수 후보\n━━━━━━━━━━━━━\n(30주선 위 + 점수 기준)\n"
    msg3 += "\n🇰🇷 한국 TOP 3\n"
    for i, s in enumerate(kr_top, 1):
        u = s.get("upside")
        w = s.get("weekly", {})
        msg3 += f"{i}. {s['sig']} {s['name']}({s['sym']}) {s['price']:,.0f}원\n"
        msg3 += f"   점수 {s['score']:.0f}"
        if u is not None: msg3 += f" | 여력 {u:+.0f}%"
        if w: msg3 += f" | 활성 {w['active']}"
        msg3 += "\n"
    msg3 += "\n🇺🇸 미국 TOP 3\n"
    for i, s in enumerate(us_top, 1):
        u = s.get("upside")
        w = s.get("weekly", {})
        msg3 += f"{i}. {s['sig']} {s['name']}({s['sym']}) ${s['price']:,.2f}\n"
        msg3 += f"   점수 {s['score']:.0f}"
        if u is not None: msg3 += f" | 여력 {u:+.0f}%"
        if w: msg3 += f" | 활성 {w['active']}"
        msg3 += "\n"
    # 제외된 종목 표시 (30주선 이탈)
    if kr_excluded or us_excluded:
        msg3 += "\n🚫 제외 (30주선 아래 = 매수 부적합)\n"
        for s in kr_excluded + us_excluded:
            w = s.get("weekly") or {}
            note = "완전이탈" if w.get("full_break_30") else "30주선아래"
            cur = f"${s['price']:,.2f}" if not s["sym"].endswith(".KS") else f"{s['price']:,.0f}원"
            msg3 += f"   {s['name']}({s['sym']}) {cur} - {note}\n"

    # ===== Msg 4: AI 종합 =====
    msg4 = (
        f"🤖 AI 종합 분석\n━━━━━━━━━━━━━\n\n{ai_text[:3500]}"
    )

    msgs = [msg1, msg2, msg3, msg4]
    for i, m in enumerate(msgs, 1):
        ok = tg_send(m)
        print(f"  Msg {i}/4: {'OK' if ok else 'FAIL'}")
        time.sleep(1)
    print(f"[완료] {NOW}")


if __name__ == "__main__":
    main()
