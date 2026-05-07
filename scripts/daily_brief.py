"""Daily Brief v6 - 차트(4년) + 매집신호 + 3축 변증법"""
import os
import re
import time
import json as jsonlib
import requests
import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")
TODAY = datetime.now(KST).strftime("%Y-%m-%d")
NOW = datetime.now(KST).strftime("%Y-%m-%d %H:%M KST")
WEEKDAY = datetime.now(KST).strftime("%a")

GEMINI_KEY = os.environ["GEMINI_API_KEY"]
OPENROUTER_KEY = os.environ["OPENROUTER_API_KEY"]
TG_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TG_CHAT = os.environ["TELEGRAM_CHAT_ID"]
HOLDINGS = jsonlib.loads(os.environ.get("HOLDINGS_JSON", "[]"))

UA = {"User-Agent": "Mozilla/5.0"}

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
# 1. 시세 + 펀더멘털 (기존)
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
    except Exception:
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
    for s in stocks:
        try:
            t = yf.Ticker(s["yf_sym"])
            info = t.info
            s["forward_pe"] = info.get("forwardPE")
            s["peg"] = info.get("pegRatio")
            s["eps_growth"] = info.get("earningsGrowth")
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


# ============================================================
# 2. 4년 일봉 + 보조지표 자체 계산 (NEW)
# ============================================================
def fetch_history_4y(symbol):
    """4년치 일봉 OHLCV 데이터."""
    try:
        df = yf.download(symbol, period="4y", interval="1d",
                         progress=False, auto_adjust=False, threads=False)
        if df is None or df.empty:
            return None
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = [c[0] for c in df.columns]
        return df
    except Exception:
        return None


def calc_indicators(df):
    """RSI / MACD / 이평선 / 볼린저밴드 자체 계산."""
    if df is None or len(df) < 60:
        return {}

    close = df["Close"]
    high = df["High"]
    low = df["Low"]
    vol = df["Volume"]

    # 이동평균선
    ma5 = close.rolling(5).mean()
    ma20 = close.rolling(20).mean()
    ma60 = close.rolling(60).mean()
    ma120 = close.rolling(120).mean()

    # RSI(14)
    delta = close.diff()
    gain = delta.where(delta > 0, 0)
    loss = -delta.where(delta < 0, 0)
    avg_gain = gain.rolling(14).mean()
    avg_loss = loss.rolling(14).mean()
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))

    # MACD
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    signal = macd.ewm(span=9, adjust=False).mean()

    # 볼린저밴드 (20, 2σ)
    bb_mid = close.rolling(20).mean()
    bb_std = close.rolling(20).std()
    bb_upper = bb_mid + 2 * bb_std
    bb_lower = bb_mid - 2 * bb_std
    bb_pos = ((close - bb_lower) / (bb_upper - bb_lower) * 100).iloc[-1]

    # 거래량 평균 대비
    vol_avg20 = vol.rolling(20).mean()
    vol_ratio = vol.iloc[-1] / vol_avg20.iloc[-1] if vol_avg20.iloc[-1] else None

    cur = close.iloc[-1]
    return {
        "rsi": float(rsi.iloc[-1]) if not pd.isna(rsi.iloc[-1]) else None,
        "macd": float(macd.iloc[-1]),
        "macd_signal": float(signal.iloc[-1]),
        "macd_cross": "골든" if macd.iloc[-1] > signal.iloc[-1] and macd.iloc[-2] <= signal.iloc[-2] else (
            "데드" if macd.iloc[-1] < signal.iloc[-1] and macd.iloc[-2] >= signal.iloc[-2] else "유지"),
        "ma5": float(ma5.iloc[-1]),
        "ma20": float(ma20.iloc[-1]),
        "ma60": float(ma60.iloc[-1]),
        "ma120": float(ma120.iloc[-1]),
        "above_ma20": cur > ma20.iloc[-1],
        "above_ma60": cur > ma60.iloc[-1],
        "above_ma120": cur > ma120.iloc[-1],
        "golden_cross_5_20": ma5.iloc[-1] > ma20.iloc[-1] and ma5.iloc[-2] <= ma20.iloc[-2],
        "golden_cross_20_60": ma20.iloc[-1] > ma60.iloc[-1] and ma20.iloc[-5] <= ma60.iloc[-5],
        "bb_pos": float(bb_pos) if not pd.isna(bb_pos) else None,
        "vol_ratio": float(vol_ratio) if vol_ratio else None,
        "high_52w": float(close.tail(252).max()),
        "low_52w": float(close.tail(252).min()),
        "high_4y": float(close.max()),
    }


def chart_pattern(ind):
    """차트 신호 종합 텍스트."""
    if not ind:
        return "데이터 부족"
    bits = []
    rsi = ind.get("rsi")
    if rsi:
        if rsi > 70: bits.append(f"RSI {rsi:.0f} 과매수")
        elif rsi < 30: bits.append(f"RSI {rsi:.0f} 과매도")
        else: bits.append(f"RSI {rsi:.0f}")
    if ind.get("macd_cross") == "골든":
        bits.append("MACD 골든크로스")
    elif ind.get("macd_cross") == "데드":
        bits.append("MACD 데드크로스")
    if ind.get("golden_cross_5_20"):
        bits.append("5/20 골든")
    if ind.get("above_ma120"):
        bits.append("장기상승추세")
    elif not ind.get("above_ma60"):
        bits.append("60일선 이탈")
    bp = ind.get("bb_pos")
    if bp is not None:
        if bp > 95: bits.append("볼밴 상단터치")
        elif bp < 5: bits.append("볼밴 하단터치")
    vr = ind.get("vol_ratio")
    if vr and vr > 2:
        bits.append(f"거래량 평균{vr:.1f}배")
    return " | ".join(bits) if bits else "중립"


def chart_score(ind):
    """0~100 차트 점수."""
    if not ind:
        return 50
    s = 50
    rsi = ind.get("rsi")
    if rsi:
        if 40 < rsi < 60: s += 5
        elif rsi > 70: s -= 10
        elif rsi < 30: s += 10
    if ind.get("macd_cross") == "골든": s += 10
    elif ind.get("macd_cross") == "데드": s -= 10
    if ind.get("above_ma20"): s += 5
    if ind.get("above_ma60"): s += 5
    if ind.get("above_ma120"): s += 10
    if ind.get("golden_cross_5_20"): s += 5
    bp = ind.get("bb_pos")
    if bp is not None:
        if bp > 95: s -= 5
        elif bp < 5: s += 5
    return max(0, min(100, s))


# ============================================================
# 3. 글로벌 매집 신호 (정동교 핵심)
# ============================================================
def detect_accumulation(ind):
    """유가 상승 + 금리 무시 + 주가 상승 = 매집 신호."""
    wti = (ind.get("WTI") or {}).get("change_pct") or 0
    us10y = (ind.get("US10Y") or {}).get("change_pct") or 0
    sp500 = (ind.get("SP500") or {}).get("change_pct") or 0
    nasdaq = (ind.get("NASDAQ") or {}).get("change_pct") or 0
    vix = (ind.get("VIX") or {}).get("change_pct") or 0

    signals = []
    score = 0

    # 패턴 1: 유가↑ + 금리 무반응 + 주가↑ = 매집
    if wti >= 1.0 and abs(us10y) < 0.5 and sp500 >= 0.5:
        signals.append("🔥 매집 신호 A: 유가 상승에도 금리 잠잠 + 주가 상승. 글로벌 자금 자산매수 시작 가능성")
        score += 30

    # 패턴 2: 금리↑ + 주가↑ = 위험선호 (성장 베팅)
    if us10y >= 1.0 and sp500 >= 0.5:
        signals.append("📈 위험선호 강세: 금리 상승에도 주가 상승. 성장 베팅 자금 유입")
        score += 15

    # 패턴 3: VIX↓ + 나스닥↑ = 공포해소
    if vix <= -3 and nasdaq >= 1.0:
        signals.append("😌 공포 해소: VIX 급락 + 기술주 강세")
        score += 10

    # 패턴 4: 모든 위험자산↑ = 리스크온
    if sp500 >= 1.0 and nasdaq >= 1.0 and (ind.get("BTC") or {}).get("change_pct", 0) >= 1.0:
        signals.append("🌐 리스크온: 주식+암호화폐 동반 강세")
        score += 10

    # 부정 신호
    if vix >= 3 and sp500 <= -1:
        signals.append("⚠️ 위험회피: VIX 급등 + 주가 하락")
        score -= 20

    if us10y >= 2 and sp500 <= -1:
        signals.append("⚠️ 금리 충격: 금리 급등으로 주가 하락")
        score -= 15

    return signals, score


# ============================================================
# 4. 점수 계산 + 시그널 (기존)
# ============================================================
def fmt_rec_dist(s):
    rd = s.get("rec_dist")
    if not rd or sum(rd.values()) == 0:
        return f"애널 {s.get('analysts') or 0}명"
    total = sum(rd.values())
    return (f"애널 {total}명: 강매{rd['strongBuy']} 매수{rd['buy']} "
            f"홀드{rd['hold']} 매도{rd['sell']} 강매도{rd['strongSell']}")


def score_stock(s):
    score = 0
    detail = []
    up = s.get("upside") or 0
    pts = min(max(up, 0) / 30 * 30, 30)
    score += pts
    detail.append(f"여력 {up:+.1f}%")
    g = (s.get("eps_q_growth") or 0) * 100
    pts = min(max(g, 0) / 50 * 20, 20)
    score += pts
    detail.append(f"분기성장 {g:+.0f}%")
    peg = s.get("peg")
    if peg and peg > 0:
        pts = min(max(2 - peg, 0) / 2 * 15, 15)
        detail.append(f"PEG {peg:.2f}")
    else:
        pts = 0
        detail.append("PEG N/A")
    score += pts
    rec = s.get("recommend") or ""
    rec_score = {"strong_buy": 15, "buy": 12, "hold": 7, "sell": 3, "strong_sell": 0}.get(rec, 5)
    score += rec_score
    off = s.get("off_high")
    if off is not None:
        pts = max(10 + off, 0) if off > -10 else max(5 + off / 2, 0)
        pts = min(pts, 10)
    else:
        pts = 5
    score += pts
    a = s.get("analysts") or 0
    pts = min(a / 30 * 10, 10)
    score += pts
    return round(score, 1), detail


def signal_emoji(score):
    if score >= 75: return "🔥 강력 매수"
    if score >= 60: return "🟢 매수"
    if score >= 45: return "🟡 홀드"
    if score >= 30: return "🟠 매도 검토"
    return "🔴 매도"


def calc_levels(s):
    price = s["price"]
    target = s.get("target_mean") or price * 1.15
    return price, min(target, price * 1.30), price * 0.93


def holding_action(s):
    sc = s["score"]
    chg = s.get("change_pct", 0) or 0
    rd = s.get("rec_dist") or {}
    sb = rd.get("strongBuy", 0) + rd.get("buy", 0)
    total = sum(rd.values()) if rd else 0
    buy_ratio = sb / total if total else 0
    if sc >= 70 and buy_ratio >= 0.7:
        return "🔥 추가 매수 검토", "강세 + 애널 매수 컨센"
    if sc >= 55 and chg > -3:
        return "🟢 보유 유지", "건전한 펀더멘털"
    if sc >= 40:
        return "🟡 홀드", "관망 권장"
    if sc < 30 or buy_ratio < 0.3:
        return "🔴 매도 검토", "약세 또는 컨센 부정적"
    return "🟠 일부 차익실현", "혼조 신호"


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


def call_gemini(prompt, model="gemini-flash-latest", temp=0.3):
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={GEMINI_KEY}"
        r = requests.post(url, json={
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"maxOutputTokens": 2500, "temperature": temp}
        }, timeout=60)
        if r.status_code != 200: return f"[ERR Gemini {model} {r.status_code}]"
        d = r.json()
        text = d.get("candidates", [{}])[0].get("content", {}).get("parts", [{}])[0].get("text", "")
        return text if len(text) > 100 else f"[ERR Gemini {model} short]"
    except Exception as e:
        return f"[ERR Gemini: {type(e).__name__}]"


def call_openrouter(prompt, model):
    try:
        r = requests.post("https://openrouter.ai/api/v1/chat/completions",
            headers={"Authorization": f"Bearer {OPENROUTER_KEY}", "Content-Type": "application/json",
                     "HTTP-Referer": "https://github.com/kimseobi-stack/daily-brief", "X-Title": "Daily Brief"},
            json={"model": model, "messages": [{"role": "user", "content": prompt}], "max_tokens": 2500},
            timeout=120)
        if r.status_code != 200: return f"[ERR OR {model} {r.status_code}]"
        d = r.json()
        text = d.get("choices", [{}])[0].get("message", {}).get("content", "")
        return text if len(text) > 100 else f"[ERR OR short]"
    except Exception as e:
        return f"[ERR OR: {type(e).__name__}]"


def call_ai(prompt, slot):
    """3단계 fallback: 1차 모델 → 2차 모델 → 3차 Gemini."""
    if slot == 1:
        out = call_gemini(prompt, "gemini-2.0-flash", 0.3)
        if out.startswith("[ERR"):
            time.sleep(2); out = call_gemini(prompt, "gemini-flash-latest", 0.3)
        if out.startswith("[ERR"):
            time.sleep(2); out = call_openrouter(prompt, "google/gemini-2.0-flash-exp:free")
        return out
    if slot == 2:
        out = call_openrouter(prompt, "openai/gpt-oss-120b:free")
        if out.startswith("[ERR"):
            time.sleep(2); out = call_openrouter(prompt, "openai/gpt-oss-20b:free")
        if out.startswith("[ERR"):
            time.sleep(2); out = call_gemini(prompt + "\n[보수적 분석]", "gemini-flash-latest", 0.5)
        return out
    if slot == 3:
        # AI3: Llama 3.3 70B (Qwen 대체) → Z-AI GLM → Gemini
        out = call_openrouter(prompt, "meta-llama/llama-3.3-70b-instruct:free")
        if out.startswith("[ERR"):
            time.sleep(2); out = call_openrouter(prompt, "z-ai/glm-4.5-air:free")
        if out.startswith("[ERR"):
            time.sleep(2); out = call_openrouter(prompt, "nvidia/nemotron-nano-9b-v2:free")
        if out.startswith("[ERR"):
            time.sleep(2); out = call_gemini(prompt + "\n[공격적 분석]", "gemini-flash-latest", 0.7)
        return out


def tg_send(text):
    text = text[:4000]
    r = requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
        json={"chat_id": TG_CHAT, "text": text, "disable_web_page_preview": True}, timeout=30)
    return r.json().get("ok")


def main():
    print(f"[{NOW}] Daily Brief v6")
    ind = fetch_indices()
    kr = fetch_fundamentals(fetch_prices(KR_STOCKS))
    us = fetch_fundamentals(fetch_prices(US_STOCKS))

    holdings_stocks = []
    for h in HOLDINGS:
        d = yf_price(h["sym"])
        if d.get("price"):
            d["sym"] = h["sym"]
            d["yf_sym"] = h["sym"]
            d["name"] = h["name"]
            d["qty"] = h.get("qty", 0)
            holdings_stocks.append(d)
        time.sleep(0.05)
    holdings_stocks = fetch_fundamentals(holdings_stocks)
    print(f"  KR={len(kr)} US={len(us)} 보유={len(holdings_stocks)}")

    for s in kr + us + holdings_stocks:
        s["score"], s["detail"] = score_stock(s)
        s["signal"] = signal_emoji(s["score"])

    for s in holdings_stocks:
        s["action"], s["action_reason"] = holding_action(s)

    kr_top = sorted(kr, key=lambda x: x["score"], reverse=True)[:5]
    us_top = sorted(us, key=lambda x: x["score"], reverse=True)[:5]

    # 4년 일봉 + 보조지표 (보유 + TOP 5 한국 + TOP 5 미국)
    print("[차트] 4년 일봉 + 보조지표 계산...")
    chart_targets = holdings_stocks + kr_top + us_top
    seen = set()
    unique_targets = []
    for s in chart_targets:
        if s["yf_sym"] not in seen:
            unique_targets.append(s)
            seen.add(s["yf_sym"])
    for s in unique_targets:
        df = fetch_history_4y(s["yf_sym"])
        s["ind"] = calc_indicators(df)
        s["chart_pattern"] = chart_pattern(s["ind"])
        s["chart_score"] = chart_score(s["ind"])
        time.sleep(0.1)

    # 매집 신호
    accum_signals, accum_score = detect_accumulation(ind)

    news = fetch_news()

    # 3축 변증법 프롬프트 (사실 100% 강제)
    base_prompt = (
        f"한미 주식 3축 변증법 분석 ({TODAY}).\n\n"
        "절대 규칙:\n"
        "1. 추론에 추론 금지. 모든 근거는 아래 제공된 사실만 사용.\n"
        "2. 모르는 종목/숫자는 '확인 필요'로 표시.\n"
        "3. 일반론 회피 금지 (예: '미국 시장 호조' 같은 두루뭉술 X).\n"
        "4. 차트/펀더/시황 3축이 충돌하면 충돌을 명시하고 어느 축이 강한지 판단.\n\n"
        f"[시황 축]\n"
        f"KOSPI {ind['KOSPI']['price']:,.2f}({ind['KOSPI']['change_pct']:+.2f}%), "
        f"SP500 {ind['SP500']['price']:,.2f}({ind['SP500']['change_pct']:+.2f}%), "
        f"USD/KRW {ind['USDKRW']['price']:,.2f}, VIX {ind['VIX']['price']:.2f}, "
        f"WTI {ind['WTI']['price']:.2f}({ind['WTI']['change_pct']:+.2f}%), "
        f"US10Y {ind['US10Y']['price']:.2f}%({ind['US10Y']['change_pct']:+.2f}%)\n"
        f"매집 신호: {' / '.join(accum_signals) if accum_signals else '없음'} (점수 {accum_score})\n\n"
        f"[펀더 축 - 한국 TOP5]\n" + "\n".join(
            f"{s['name']}({s['sym']}) 점수{s['score']} 여력{s.get('upside') or 0:+.0f}% PEG{s.get('peg') or 0:.2f}"
            for s in kr_top) + "\n\n"
        f"[펀더 축 - 미국 TOP5]\n" + "\n".join(
            f"{s['name']}({s['sym']}) 점수{s['score']} 여력{s.get('upside') or 0:+.0f}%"
            for s in us_top) + "\n\n"
        f"[차트 축 - 보유+TOP10 패턴]\n" + "\n".join(
            f"{s['name']}({s['sym']}): {s.get('chart_pattern','N/A')} (차트점수 {s.get('chart_score',50)})"
            for s in unique_targets[:15]) + "\n\n"
        f"[뉴스 헤드라인]\n" + " | ".join(news[:15]) + "\n\n"
        "출력 (1500자 이내):\n"
        "📊 시장 진단 (시황축)\n"
        "🎯 한국 매수 3개 (3축 합의 종목, 충돌 시 명시)\n"
        "🎯 미국 매수 3개\n"
        "⚠️ 회피 종목 (3축 부정)\n"
        "🔮 일주일 전망"
    )

    print("[AI] 3축 변증법 분석...")
    ai = []
    for slot in [1, 2, 3]:
        r = call_ai(base_prompt, slot)
        ai.append(r)
        print(f"  AI{slot}: {len(r)}자")
        time.sleep(2)
    valid = sum(1 for r in ai if not r.startswith("[ERR") and len(r) > 200)

    meta = (
        "3 AI 답변 종합. 2/3 합의만 채택. 단독 의견 폐기. 사실 100% 검증.\n\n"
        f"[AI1]\n{ai[0]}\n[AI2]\n{ai[1]}\n[AI3]\n{ai[2]}\n\n"
        "출력:\n📊 시장 진단\n🎯 한국 매수 3 (3축 합의)\n🎯 미국 매수 3\n⚠️ 회피\n🔮 전망"
    )
    final = call_gemini(meta)
    if final.startswith("[ERR"):
        final = next((r for r in ai if not r.startswith("[ERR")), "분석 실패")

    # ===== Msg 0: 보유 진단 (차트 추가) =====
    msg0 = "💼 내 보유 종목 진단\n━━━━━━━━━━━━━━━\n(시세+펀더+차트+애널 종합)\n\n"
    if not holdings_stocks:
        msg0 += "보유 종목 없음\n"
    else:
        for s in sorted(holdings_stocks, key=lambda x: x["score"], reverse=True):
            sd = s["sym"].replace(".KS", "")
            cur = f"${s['price']:,.2f}" if not s["sym"].endswith(".KS") else f"{s['price']:,.0f}원"
            msg0 += "━━━━━━━━━━━━━━━\n"
            msg0 += f"{s['action']}  점수 {s['score']}/100\n"
            msg0 += f"{s['name']} ({sd})  보유 {s['qty']}주\n"
            msg0 += f"현재 {cur}  ({s.get('change_pct') or 0:+.2f}%)\n"
            if s.get("target_mean"):
                tg = f"${s['target_mean']:,.2f}" if not s["sym"].endswith(".KS") else f"{s['target_mean']:,.0f}원"
                msg0 += f"목표 {tg}  여력 {s.get('upside') or 0:+.1f}%\n"
            msg0 += f"📈 차트: {s.get('chart_pattern','N/A')} (점수 {s.get('chart_score',50)})\n"
            msg0 += f"👥 {fmt_rec_dist(s)}\n"
            msg0 += f"💡 {s['action_reason']}\n\n"

    # ===== Msg 1: 거시 + 매집 신호 =====
    msg1 = (
        f"🌅 Daily Brief v6\n📅 {TODAY} ({WEEKDAY})  📡 {NOW}\n━━━━━━━━━━━━━━━\n📊 거시 지표\n\n"
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
        f"BTC     ${ind['BTC']['price']:>9,.0f}  {ind['BTC']['change_pct']:+.2f}%\n\n"
        f"━━━━━━━━━━━━━━━\n🌐 글로벌 자금 매집 신호 (정동교 트리거)\n\n"
    )
    if accum_signals:
        for sig in accum_signals:
            msg1 += f"{sig}\n\n"
        msg1 += f"종합 매집 점수: {accum_score:+d}\n"
    else:
        msg1 += "오늘 특이 매집 신호 없음. 일반 흐름.\n"

    # ===== Msg 2: 한국 매수 후보 + 차트 =====
    msg2 = "🇰🇷 한국 매수 후보 TOP 5\n━━━━━━━━━━━━━━━\n(펀더+차트+애널 종합)\n\n"
    for i, s in enumerate(kr_top, 1):
        e, tp, sl = calc_levels(s)
        msg2 += f"{i}. {s['signal']}  펀더{s['score']} / 차트{s.get('chart_score',50)}\n"
        msg2 += f"   {s['name']} ({s['sym']})\n"
        msg2 += f"   현재 {s['price']:,.0f}원"
        if s.get("target_mean"):
            msg2 += f" → 목표 {s['target_mean']:,.0f}원 ({s.get('upside') or 0:+.1f}%)"
        msg2 += "\n"
        msg2 += f"   진입 {e:,.0f} / 익절 {tp:,.0f} / 손절 {sl:,.0f}\n"
        msg2 += f"   📈 {s.get('chart_pattern','N/A')}\n"
        msg2 += f"   👥 {fmt_rec_dist(s)}\n"
        msg2 += f"   {' | '.join(s['detail'][:3])}\n\n"

    # ===== Msg 3: 미국 매수 후보 + 차트 =====
    msg3 = "🇺🇸 미국 매수 후보 TOP 5\n━━━━━━━━━━━━━━━\n(펀더+차트+애널 종합)\n\n"
    for i, s in enumerate(us_top, 1):
        e, tp, sl = calc_levels(s)
        msg3 += f"{i}. {s['signal']}  펀더{s['score']} / 차트{s.get('chart_score',50)}\n"
        msg3 += f"   {s['name']} ({s['sym']})\n"
        msg3 += f"   현재 ${s['price']:,.2f}"
        if s.get("target_mean"):
            msg3 += f" → 목표 ${s['target_mean']:,.2f} ({s.get('upside') or 0:+.1f}%)"
        msg3 += "\n"
        msg3 += f"   진입 ${e:,.2f} / 익절 ${tp:,.2f} / 손절 ${sl:,.2f}\n"
        msg3 += f"   📈 {s.get('chart_pattern','N/A')}\n"
        msg3 += f"   👥 {fmt_rec_dist(s)}\n"
        msg3 += f"   {' | '.join(s['detail'][:3])}\n\n"

    # ===== Msg 4: 한국 등락 =====
    kr_sorted = sorted(kr, key=lambda x: x.get("change_pct", 0) or 0, reverse=True)
    msg4 = "🇰🇷 한국 등락\n━━━━━━━━━━━━━━━\n📈 상승 TOP 8\n\n"
    for s in kr_sorted[:8]:
        c = s.get('change_pct') or 0
        msg4 += f"{s['name']:<8}({s['sym']}) {s['price']:>9,.0f}원 {c:+6.2f}% {s['signal'].split()[0]}\n"
    msg4 += "\n📉 하락 TOP 8\n\n"
    for s in kr_sorted[-8:][::-1]:
        c = s.get('change_pct') or 0
        msg4 += f"{s['name']:<8}({s['sym']}) {s['price']:>9,.0f}원 {c:+6.2f}% {s['signal'].split()[0]}\n"

    # ===== Msg 5: 미국 등락 =====
    us_sorted = sorted(us, key=lambda x: x.get("change_pct", 0) or 0, reverse=True)
    msg5 = "🇺🇸 미국 등락\n━━━━━━━━━━━━━━━\n📈 상승 TOP 8\n\n"
    for s in us_sorted[:8]:
        c = s.get('change_pct') or 0
        msg5 += f"{s['name']:<8}({s['sym']:<5}) ${s['price']:>8,.2f} {c:+6.2f}% {s['signal'].split()[0]}\n"
    msg5 += "\n📉 하락 TOP 8\n\n"
    for s in us_sorted[-8:][::-1]:
        c = s.get('change_pct') or 0
        msg5 += f"{s['name']:<8}({s['sym']:<5}) ${s['price']:>8,.2f} {c:+6.2f}% {s['signal'].split()[0]}\n"

    # ===== Msg 6: 3축 변증법 AI 분석 =====
    msg6 = (
        f"🤖 AI 3축 변증법 분석 (유효 {valid}/3)\n━━━━━━━━━━━━━━━\n"
        f"(차트 + 펀더 + 시황 충돌 검증)\n\n{final}\n\n"
        f"━━━━━━━━━━━━━━━\n📡 Yahoo Finance / 🤖 Gemini+OpenRouter\n책임: 본인"
    )

    # ===== Msg 7: 최종 액션 플랜 =====
    msg7 = "🎯 오늘의 최종 액션 플랜\n━━━━━━━━━━━━━━━\n\n"
    add_buy = [s for s in holdings_stocks if "추가 매수" in s.get('action', '')]
    hold = [s for s in holdings_stocks if "보유 유지" in s.get('action', '') or "홀드" in s.get('action', '')]
    take_profit = [s for s in holdings_stocks if "차익실현" in s.get('action', '')]
    sell = [s for s in holdings_stocks if "매도 검토" in s.get('action', '')]

    msg7 += "📌 보유 종목\n\n"
    if add_buy:
        msg7 += "🔥 추가 매수 검토\n"
        for s in add_buy:
            msg7 += f"  • {s['name']}({s['sym'].replace('.KS','')}) - {s['action_reason']}\n"
        msg7 += "\n"
    if hold:
        msg7 += "🟢 보유 유지\n"
        for s in hold:
            msg7 += f"  • {s['name']}({s['sym'].replace('.KS','')})\n"
        msg7 += "\n"
    if take_profit:
        msg7 += "🟠 일부 차익실현\n"
        for s in take_profit:
            msg7 += f"  • {s['name']}({s['sym'].replace('.KS','')}) - {s['action_reason']}\n"
        msg7 += "\n"
    if sell:
        msg7 += "🔴 매도 검토\n"
        for s in sell:
            msg7 += f"  • {s['name']}({s['sym'].replace('.KS','')}) - {s['action_reason']}\n"
        msg7 += "\n"

    msg7 += "📌 신규 매수 후보\n\n🇰🇷 한국 TOP 3\n"
    for i, s in enumerate(kr_top[:3], 1):
        msg7 += f"  {i}. {s['name']}({s['sym']}) 펀더{s['score']}/차트{s.get('chart_score',50)}  여력{s.get('upside') or 0:+.0f}%\n"
    msg7 += "\n🇺🇸 미국 TOP 3\n"
    for i, s in enumerate(us_top[:3], 1):
        msg7 += f"  {i}. {s['name']}({s['sym']}) 펀더{s['score']}/차트{s.get('chart_score',50)}  여력{s.get('upside') or 0:+.0f}%\n"

    msg7 += (
        "\n━━━━━━━━━━━━━━━\n📋 결정 가이드\n"
        "• 위 액션은 시스템 권고 (3축 변증)\n"
        "• 차트+펀더+시황 충돌 시 직접 판단\n"
        "• 본인 진행/수정/삭제\n"
        "• 매매 책임: 본인"
    )

    msgs = [msg0, msg1, msg2, msg3, msg4, msg5, msg6, msg7]
    for i, m in enumerate(msgs, 1):
        ok = tg_send(m)
        print(f"  Msg {i}/{len(msgs)}: {'OK' if ok else 'FAIL'}")
        time.sleep(1)
    print(f"[완료] {NOW}")


if __name__ == "__main__":
    main()
