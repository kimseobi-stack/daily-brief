"""Daily Brief v9.2 - ROE + 가치투자 4지표(PBR/PSR/PEG/부채) + 절대 손실 방지"""
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


KIWOOM_TOKEN = kw.get_token() if KIWOOM_AVAILABLE else None
print(f"  Kiwoom Token: {'OK' if KIWOOM_TOKEN else 'NONE'}")


def yf_price_safe(symbol):
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
        try:
            df = yf.download(symbol, period="5d", interval="1d", progress=False, auto_adjust=False, threads=False)
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


def value_check(s):
    """가치투자 4지표 카운트. 만족 = 1, 미만족/데이터 없음 = 0."""
    pbr_ok = bool(s.get("pb") and 0 < s["pb"] < 1)
    psr_ok = bool(s.get("psr") and 0 < s["psr"] < 1)
    peg_ok = bool(s.get("peg") and 0 < s["peg"] < 1)
    debt_ok = bool(s.get("debt_ratio") is not None and s["debt_ratio"] < 100)  # D/E 100% 미만 = 부채<자본
    return {
        "pbr": pbr_ok, "psr": psr_ok, "peg": peg_ok, "debt": debt_ok,
        "count": int(pbr_ok) + int(psr_ok) + int(peg_ok) + int(debt_ok),
    }


def fetch_fundamentals(stocks):
    for s in stocks:
        try:
            t = yf.Ticker(s["yf_sym"])
            info = t.info
            s["roe"] = info.get("returnOnEquity")
            s["pe"] = info.get("trailingPE")
            s["fwd_pe"] = info.get("forwardPE")
            s["pb"] = info.get("priceToBook")
            s["psr"] = info.get("priceToSalesTrailing12Months")
            s["peg"] = info.get("pegRatio") or info.get("trailingPegRatio")
            s["debt_ratio"] = info.get("debtToEquity")  # % 단위
            s["eps_q_growth"] = info.get("earningsQuarterlyGrowth")
            s["target_mean"] = info.get("targetMeanPrice")
            s["recommend"] = info.get("recommendationKey")
            s["analysts"] = info.get("numberOfAnalystOpinions")
            s["52w_high"] = info.get("fiftyTwoWeekHigh")
            s["upside"] = (s["target_mean"] / s["price"] - 1) * 100 if s.get("target_mean") and s.get("price") else None
            s["off_high"] = (s["price"] / s["52w_high"] - 1) * 100 if s.get("52w_high") and s.get("price") else None
            s["ma50"] = info.get("fiftyDayAverage")
            s["ma200"] = info.get("twoHundredDayAverage")
            s["above_50d"] = bool(s.get("ma50") and s.get("price") and s["price"] > s["ma50"])
            s["above_200d"] = bool(s.get("ma200") and s.get("price") and s["price"] > s["ma200"])
            avg_vol = info.get("averageVolume")
            cur_vol = info.get("regularMarketVolume") or info.get("volume")
            s["vol_ratio"] = (cur_vol / avg_vol) if avg_vol and cur_vol else None
            s["value"] = value_check(s)
            # 검증: ROE+PE+200일선+PBR+부채비율 모두 있어야 verified
            s["verified"] = bool(
                s.get("roe") is not None
                and s.get("pe") and s["pe"] > 0
                and s.get("pb") and s["pb"] > 0
                and s.get("ma200")
                and s.get("debt_ratio") is not None
                and s.get("price")
            )
        except Exception:
            s["verified"] = False
            s["value"] = {"pbr": False, "psr": False, "peg": False, "debt": False, "count": 0}
        time.sleep(0.15)
    return stocks


def holding_action_v9(s):
    pl = s.get("kw_pl_pct")
    score = s.get("score", 50)
    above_200 = s.get("above_200d", True)
    above_50 = s.get("above_50d", True)
    if pl is not None:
        if pl <= -7: return "🔴 손절 매도 (-7% 도달)"
        if pl >= 25: return "🟢 2차 익절 (보유 1/2 매도)"
        if pl >= 15: return "🟢 1차 익절 (보유 1/3 매도)"
    if not above_200: return "🔴 장기 추세 이탈 (200일선 아래) - 매도 검토"
    if not above_50 and score < 50: return "🟠 50일선 이탈 + 약점수 - 비중 축소"
    if score >= 75: return "🔥 추가 매수"
    if score >= 60: return "🟢 보유 유지"
    if score >= 45: return "🟡 홀드 (관망)"
    return "🟠 비중 축소"


def score_stock(s):
    """v9.2: ROE 20점 + 가치 4지표 20점 + 추세 25점 + 컨센 20점 + 모멘텀 15점."""
    score = 0
    # ROE (20점)
    roe_pct = (s.get("roe") or 0) * 100
    score += min(max(roe_pct, 0) / 25 * 20, 20)
    # 가치투자 4지표 (20점, 각 5점)
    v = s.get("value") or {}
    score += sum([5 if v.get(k) else 0 for k in ["pbr", "psr", "peg", "debt"]])
    # 수급/기술 (25점)
    if s.get("above_50d"): score += 8
    if s.get("above_200d"): score += 10
    vr = s.get("vol_ratio") or 1.0
    if vr >= 1.5: score += 7
    elif vr >= 1.2: score += 5
    elif vr >= 1.0: score += 3
    # 컨센서스 (20점)
    rec = s.get("recommend") or ""
    score += {"strong_buy": 10, "buy": 8, "hold": 5, "sell": 2, "strong_sell": 0}.get(rec, 5)
    upside = s.get("upside") or 0
    score += min(max(upside, 0) / 30 * 10, 10)
    # 모멘텀 + 성장 (15점)
    eps_g = (s.get("eps_q_growth") or 0) * 100
    score += min(max(eps_g, 0) / 50 * 5, 5)
    chg = s.get("change_pct") or 0
    if chg > 0: score += min(chg / 5 * 10, 10)
    elif chg > -2: score += 3
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


def macro_risk(ind):
    """거시 환경 위험 체크. 위험 시 추천 보류 권고."""
    vix = (ind.get("VIX") or {}).get("price") or 0
    us10y_chg = (ind.get("US10Y") or {}).get("change_pct") or 0
    risks = []
    if vix >= 30:
        risks.append(f"⚠️ VIX {vix:.1f} 공포 구간 (30+)")
    if us10y_chg >= 3:
        risks.append(f"⚠️ US10Y 급등 {us10y_chg:+.2f}% (성장주 위험)")
    return risks


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


def fmt_line(i, s, kr=True):
    """v9.2: ROE + 가치 4지표 카운트 + 4지표 모두 표시."""
    price_str = f"{s['price']:,.0f}원" if kr else f"${s['price']:,.2f}"
    line = f"{i}. {s['sig']} {s['name']}({s['sym']}) {price_str}\n"
    line += f"   ⭐ ROE {s['roe']*100:.1f}% | 점수 {s['score']:.0f} | 가치 {s['value']['count']}/4\n"
    # 4지표 + PE
    parts = []
    if s.get("pe") and s["pe"] > 0: parts.append(f"PE {s['pe']:.1f}")
    if s.get("pb") and s["pb"] > 0: parts.append(f"PBR {s['pb']:.1f}{'✓' if s['value']['pbr'] else ''}")
    if s.get("psr") and s["psr"] > 0: parts.append(f"PSR {s['psr']:.1f}{'✓' if s['value']['psr'] else ''}")
    if s.get("peg") and s["peg"] > 0: parts.append(f"PEG {s['peg']:.1f}{'✓' if s['value']['peg'] else ''}")
    if s.get("debt_ratio") is not None:
        parts.append(f"부채 {s['debt_ratio']:.0f}%{'✓' if s['value']['debt'] else ''}")
    if parts: line += f"   {' | '.join(parts)}\n"
    if s.get("upside") is not None:
        line += f"   여력 {s['upside']:+.0f}% | 추세 {'200↑' if s['above_200d'] else '200↓'}/{'50↑' if s['above_50d'] else '50↓'}\n"
    return line


def main():
    print(f"[{NOW}] Daily Brief v9.2 - {SESSION}")
    ind = fetch_indices()
    kr = fetch_fundamentals(fetch_prices(KR_STOCKS))
    us = fetch_fundamentals(fetch_prices(US_STOCKS))

    holdings_stocks = []
    kw_balance = None
    kw_deposit = 0
    kw_kr_syms = set()
    if KIWOOM_TOKEN:
        kw_balance = kw.fetch_balance(KIWOOM_TOKEN)
        if kw_balance:
            kw_deposit = kw_balance.get("deposit", 0)
            for h in kw_balance.get("holdings", []):
                if not h.get("sym"): continue
                ks_sym = f"{h['sym']}.KS"
                holdings_stocks.append({
                    "sym": ks_sym, "yf_sym": ks_sym,
                    "name": h["name"], "qty": h["qty"],
                    "avg_price": h["avg_price"], "price": h["cur_price"],
                    "prev": None, "change_pct": None,
                    "eval_amt": h["eval_amt"], "pl_amt": h["pl_amt"],
                    "kw_pl_pct": h["pl_pct"], "_from_kiwoom": True,
                })
            kw_kr_syms = {h["sym"] for h in kw_balance.get("holdings", [])}

    for h in HOLDINGS:
        sym_clean = h["sym"].replace(".KS", "")
        if sym_clean in kw_kr_syms: continue
        d = yf_price_safe(h["sym"])
        if d.get("price"):
            d["sym"] = h["sym"]; d["yf_sym"] = h["sym"]
            d["name"] = h["name"]; d["qty"] = h.get("qty", 0)
            d["avg_price"] = h.get("avg_price")
            holdings_stocks.append(d)
        time.sleep(0.05)
    holdings_stocks = fetch_fundamentals(holdings_stocks)

    for s in kr + us + holdings_stocks:
        s["score"] = score_stock(s)
        s["sig"] = signal_emoji(s["score"])
    for s in holdings_stocks:
        s["action"] = holding_action_v9(s)

    # v9.2: 절대 손실 방지 필터 - verified + ROE 10%+ + 200일선 + 부채<200%
    def is_quality(s):
        return (s.get("verified")
                and s.get("above_200d")
                and (s.get("roe") or 0) * 100 >= 10
                and (s.get("debt_ratio") or 999) < 200)  # 부채비율 200% 미만 = 안전선
    kr_cand = sorted(kr, key=lambda x: x["score"], reverse=True)
    us_cand = sorted(us, key=lambda x: x["score"], reverse=True)
    kr_top = [s for s in kr_cand if is_quality(s)][:5]
    us_top = [s for s in us_cand if is_quality(s)][:5]

    def excl_reason(s):
        if not s.get("verified"): return "데이터 부족"
        if not s.get("above_200d"): return "200일선 아래 (추세 이탈)"
        if (s.get("roe") or 0) * 100 < 10: return f"ROE {(s.get('roe') or 0)*100:.1f}% (10% 미만)"
        if (s.get("debt_ratio") or 999) >= 200: return f"부채비율 {s.get('debt_ratio'):.0f}% (200% 이상)"
        return None
    kr_excl = [(s, excl_reason(s)) for s in kr_cand if s not in kr_top and excl_reason(s)][:4]
    us_excl = [(s, excl_reason(s)) for s in us_cand if s not in us_top and excl_reason(s)][:4]

    accum = detect_accumulation(ind)
    risks = macro_risk(ind)
    news = fetch_news()

    holdings_brief = " | ".join(
        f"{s['name']}({(s.get('action','') or '').split()[0]} 점{s['score']:.0f})"
        for s in holdings_stocks[:8]) if holdings_stocks else "없음"
    ai_prompt = (
        f"한미 주식 분석 ({TODAY} {SESSION}). 추측 금지, 사실만, 손실 방지 우선.\n"
        f"거시: KOSPI {ind['KOSPI']['price']:.2f}({ind['KOSPI']['change_pct']:+.2f}%), "
        f"SP500 {ind['SP500']['price']:.2f}({ind['SP500']['change_pct']:+.2f}%), "
        f"USDKRW {ind['USDKRW']['price']:.2f}, US10Y {ind['US10Y']['price']:.2f}%, "
        f"VIX {ind['VIX']['price']:.2f}\n"
        f"매집신호: {' / '.join(accum) if accum else '없음'}\n"
        f"거시리스크: {' / '.join(risks) if risks else '없음'}\n"
        f"보유: {holdings_brief}\n"
        f"한국TOP5: " + ", ".join(f"{s['name']}(ROE{s['roe']*100:.0f}% 가치{s['value']['count']}/4 점{s['score']:.0f})" for s in kr_top) + "\n"
        f"미국TOP5: " + ", ".join(f"{s['name']}(ROE{s['roe']*100:.0f}% 가치{s['value']['count']}/4 점{s['score']:.0f})" for s in us_top) + "\n"
        f"뉴스: " + " | ".join(news[:10]) + "\n\n"
        "출력 (800자 이내, 손실 방지 우선):\n"
        "📊 시장 한줄 (거시)\n"
        "💼 보유 종목 액션\n"
        "🇰🇷 한국 1픽 + ROE/PBR/부채 근거\n"
        "🇺🇸 미국 1픽 + ROE/PBR/부채 근거\n"
        "⚠️ 리스크 (구체적)\n"
        "🔮 다음 흐름"
    )
    ai_text = best_ai(ai_prompt)
    print(f"  AI 답변: {len(ai_text)}자")

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
    msg1 += "🌐 매집 신호\n" + "\n".join(accum) + "\n" if accum else "🌐 매집 신호: 없음\n"
    if risks:
        msg1 += "\n🚨 거시 리스크 (신규 매수 보수적)\n" + "\n".join(risks) + "\n"
    if kw_balance:
        env_label = "모의" if os.environ.get("KIWOOM_BASE", "mock") == "mock" else "실전"
        msg1 += f"\n💰 키움 계좌 ({env_label})\n"
        msg1 += f"예수금  {kw_deposit:>14,}원\n"
        msg1 += f"평가금  {kw_balance.get('tot_eval', 0):>14,}원\n"
        msg1 += f"평가손익 {kw_balance.get('tot_pl', 0):>+13,}원 ({kw_balance.get('tot_pl_pct', 0):+.2f}%)\n"

    msg2 = "💼 보유 종목 진단\n━━━━━━━━━━━━━\n"
    if not holdings_stocks:
        msg2 += "\n보유 종목 없음\n"
    for s in sorted(holdings_stocks, key=lambda x: 0 if "손절" in x.get("action","") else 1):
        cur = f"${s['price']:,.2f}" if not s["sym"].endswith(".KS") else f"{s['price']:,.0f}원"
        chg = s.get("change_pct") or 0
        src = " 🟢키움" if s.get("_from_kiwoom") else ""
        msg2 += f"\n{s.get('action','')}{src}\n"
        msg2 += f"{s['name']} ({s['sym'].replace('.KS','')}) {s.get('qty',0)}주\n"
        msg2 += f"가격 {cur} ({chg:+.2f}%) | 점수 {s['score']:.0f}/100\n"
        if s.get("avg_price"):
            avg = s["avg_price"]
            avg_str = f"${avg:,.2f}" if not s["sym"].endswith(".KS") else f"{avg:,.0f}원"
            msg2 += f"평균단가 {avg_str}"
            if s.get("kw_pl_pct") is not None:
                msg2 += f" | 손익 {s['kw_pl_pct']:+.2f}%"
            msg2 += "\n"
        if s.get("roe") is not None:
            msg2 += f"⭐ ROE {s['roe']*100:+.1f}%"
            v = s.get("value") or {}
            if v.get("count") is not None:
                msg2 += f" | 가치 {v['count']}/4"
            msg2 += "\n"
        parts = []
        if s.get("pe") and s["pe"] > 0: parts.append(f"PE {s['pe']:.1f}")
        if s.get("pb") and s["pb"] > 0: parts.append(f"PBR {s['pb']:.1f}")
        if s.get("psr") and s["psr"] > 0: parts.append(f"PSR {s['psr']:.1f}")
        if s.get("peg") and s["peg"] > 0: parts.append(f"PEG {s['peg']:.1f}")
        if s.get("debt_ratio") is not None: parts.append(f"부채 {s['debt_ratio']:.0f}%")
        if parts: msg2 += " | ".join(parts) + "\n"
        trend = []
        if s.get("above_50d") is not None:
            trend.append(f"50일선 {'위' if s['above_50d'] else '아래'}")
        if s.get("above_200d") is not None:
            trend.append(f"200일선 {'위' if s['above_200d'] else '아래'}")
        if trend: msg2 += "추세 " + " | ".join(trend) + "\n"
        if s.get("upside") is not None:
            msg2 += f"애널 목표가 여력 {s['upside']:+.0f}%\n"

    msg3 = "🎯 신규 매수 후보 (절대 검증)\n"
    msg3 += "조건: ROE 10%+ & 부채 200%- & 200일선 위 & 데이터 검증\n━━━━━━━━━━━━━\n\n🇰🇷 한국 TOP 5\n"
    if kr_top:
        for i, s in enumerate(kr_top, 1):
            msg3 += fmt_line(i, s, kr=True)
    else:
        msg3 += "조건 충족 종목 없음 (보수적 기다림)\n"
    msg3 += "\n🇺🇸 미국 TOP 5\n"
    if us_top:
        for i, s in enumerate(us_top, 1):
            msg3 += fmt_line(i, s, kr=False)
    else:
        msg3 += "조건 충족 종목 없음\n"
    if kr_excl or us_excl:
        msg3 += "\n🚫 제외 (사유)\n"
        for s, r in (kr_excl + us_excl)[:6]:
            msg3 += f"  {s['name']}({s['sym']}) - {r}\n"
    msg3 += "\n📌 가치✓ = 4지표(PBR/PSR/PEG/부채) 통과\n"

    msg4 = f"🤖 AI 종합 분석\n━━━━━━━━━━━━━\n\n{ai_text[:3500]}"

    for i, m in enumerate([msg1, msg2, msg3, msg4], 1):
        ok = tg_send(m)
        print(f"  Msg {i}/4: {'OK' if ok else 'FAIL'}")
        time.sleep(1)
    print(f"[완료] {NOW}")


if __name__ == "__main__":
    main()
