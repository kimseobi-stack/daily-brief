"""Daily Brief v5 - 보유 종목 진단 + 액션 플랜 추가"""
import os
import re
import time
import json as jsonlib
import requests
import yfinance as yf
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
    detail.append(f"추천 {rec or 'N/A'}")
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
            "generationConfig": {"maxOutputTokens": 2000, "temperature": temp}
        }, timeout=60)
        if r.status_code != 200: return f"[ERROR Gemini {model} HTTP {r.status_code}]"
        d = r.json()
        text = d.get("candidates", [{}])[0].get("content", {}).get("parts", [{}])[0].get("text", "")
        return text if len(text) > 100 else f"[ERROR Gemini {model} short]"
    except Exception as e:
        return f"[ERROR Gemini: {type(e).__name__}]"


def call_openrouter(prompt, model):
    try:
        r = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={"Authorization": f"Bearer {OPENROUTER_KEY}", "Content-Type": "application/json",
                     "HTTP-Referer": "https://github.com/kimseobi-stack/daily-brief", "X-Title": "Daily Brief"},
            json={"model": model, "messages": [{"role": "user", "content": prompt}], "max_tokens": 2000},
            timeout=120)
        if r.status_code != 200: return f"[ERROR OR {model} HTTP {r.status_code}]"
        d = r.json()
        text = d.get("choices", [{}])[0].get("message", {}).get("content", "")
        return text if len(text) > 100 else f"[ERROR OR {model} short]"
    except Exception as e:
        return f"[ERROR OR: {type(e).__name__}]"


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
            out = call_gemini(prompt + "\n[보수적]", "gemini-flash-latest", 0.5)
        return out
    if slot == 3:
        out = call_openrouter(prompt, "qwen/qwen3-next-80b-a3b-instruct:free")
        if out.startswith("[ERROR"):
            time.sleep(2)
            out = call_gemini(prompt + "\n[공격적]", "gemini-flash-latest", 0.7)
        return out


def tg_send(text):
    text = text[:4000]
    r = requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                      json={"chat_id": TG_CHAT, "text": text, "disable_web_page_preview": True}, timeout=30)
    return r.json().get("ok")


def main():
    print(f"[{NOW}] Daily Brief v5")
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
    news = fetch_news()

    base_prompt = (
        f"한미 주식 분석 ({TODAY}).\n"
        f"지수: KOSPI {ind['KOSPI']['price']:,.2f}({ind['KOSPI']['change_pct']:+.2f}%), "
        f"SP500 {ind['SP500']['price']:,.2f}({ind['SP500']['change_pct']:+.2f}%)\n"
        f"한국 TOP5: " + ", ".join(f"{s['name']}({s['sym']}) 점수{s['score']}" for s in kr_top) + "\n"
        f"미국 TOP5: " + ", ".join(f"{s['name']}({s['sym']}) 점수{s['score']}" for s in us_top) + "\n"
        f"뉴스: " + " | ".join(news[:15]) + "\n\n"
        "출력 (1500자):\n📊 시장 진단\n🎯 한국 매수 3개\n🎯 미국 매수 3개\n⚠️ 회피\n🔮 일주일 전망"
    )
    ai = []
    for slot in [1, 2, 3]:
        r = call_ai(base_prompt, slot)
        ai.append(r)
        print(f"  AI{slot}: {len(r)}자")
        time.sleep(2)
    valid = sum(1 for r in ai if not r.startswith("[ERROR") and len(r) > 200)
    meta = (
        "3 AI 답변 종합. 2/3 합의만 채택. 직설적 전문가 톤.\n\n"
        f"[AI1]\n{ai[0]}\n[AI2]\n{ai[1]}\n[AI3]\n{ai[2]}\n\n"
        "출력:\n📊 시장 진단\n🎯 한국 매수 3\n🎯 미국 매수 3\n⚠️ 회피\n🔮 전망"
    )
    final = call_gemini(meta)
    if final.startswith("[ERROR"):
        final = next((r for r in ai if not r.startswith("[ERROR")), "분석 실패")

    # Msg 0: 보유 종목 진단
    msg0 = "💼 내 보유 종목 진단\n━━━━━━━━━━━━━━━\n(시세+펀더멘털+애널+AI 종합)\n\n"
    if not holdings_stocks:
        msg0 += "보유 종목 없음 (HOLDINGS_JSON Secret 필요)\n"
    else:
        for s in sorted(holdings_stocks, key=lambda x: x["score"], reverse=True):
            sd = s["sym"].replace(".KS", "")
            cur = f"${s['price']:,.2f}" if not s["sym"].endswith(".KS") else f"{s['price']:,.0f}원"
            msg0 += f"━━━━━━━━━━━━━━━\n"
            msg0 += f"{s['action']}  점수 {s['score']}/100\n"
            msg0 += f"{s['name']} ({sd})  보유 {s['qty']}주\n"
            msg0 += f"현재 {cur}  ({s.get('change_pct') or 0:+.2f}%)\n"
            if s.get("target_mean"):
                tg = f"${s['target_mean']:,.2f}" if not s["sym"].endswith(".KS") else f"{s['target_mean']:,.0f}원"
                msg0 += f"목표 {tg}  여력 {s.get('upside') or 0:+.1f}%\n"
            msg0 += f"👥 {fmt_rec_dist(s)}\n"
            msg0 += f"💡 {s['action_reason']}\n\n"

    # Msg 1: 거시
    msg1 = (
        f"🌅 Daily Brief\n📅 {TODAY} ({WEEKDAY})  📡 {NOW}\n━━━━━━━━━━━━━━━\n📊 거시 지표\n\n"
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

    # Msg 2: 한국 추천
    msg2 = "🇰🇷 한국 매수 후보 TOP 5\n━━━━━━━━━━━━━━━\n\n"
    for i, s in enumerate(kr_top, 1):
        e, tp, sl = calc_levels(s)
        msg2 += f"{i}. {s['signal']}  점수 {s['score']}/100\n"
        msg2 += f"   {s['name']} ({s['sym']})\n"
        msg2 += f"   현재 {s['price']:,.0f}원"
        if s.get("target_mean"):
            msg2 += f" → 목표 {s['target_mean']:,.0f}원 ({s.get('upside') or 0:+.1f}%)"
        msg2 += "\n"
        msg2 += f"   진입 {e:,.0f} / 익절 {tp:,.0f} / 손절 {sl:,.0f}\n"
        msg2 += f"   👥 {fmt_rec_dist(s)}\n"
        msg2 += f"   {' | '.join(s['detail'][:4])}\n\n"

    # Msg 3: 미국 추천
    msg3 = "🇺🇸 미국 매수 후보 TOP 5\n━━━━━━━━━━━━━━━\n\n"
    for i, s in enumerate(us_top, 1):
        e, tp, sl = calc_levels(s)
        msg3 += f"{i}. {s['signal']}  점수 {s['score']}/100\n"
        msg3 += f"   {s['name']} ({s['sym']})\n"
        msg3 += f"   현재 ${s['price']:,.2f}"
        if s.get("target_mean"):
            msg3 += f" → 목표 ${s['target_mean']:,.2f} ({s.get('upside') or 0:+.1f}%)"
        msg3 += "\n"
        msg3 += f"   진입 ${e:,.2f} / 익절 ${tp:,.2f} / 손절 ${sl:,.2f}\n"
        msg3 += f"   👥 {fmt_rec_dist(s)}\n"
        msg3 += f"   {' | '.join(s['detail'][:4])}\n\n"

    # Msg 4: 한국 등락
    kr_sorted = sorted(kr, key=lambda x: x.get("change_pct", 0) or 0, reverse=True)
    msg4 = "🇰🇷 한국 시장 등락\n━━━━━━━━━━━━━━━\n📈 상승 TOP 8\n\n"
    for s in kr_sorted[:8]:
        c = s.get('change_pct') or 0
        msg4 += f"{s['name']:<8}({s['sym']}) {s['price']:>9,.0f}원 {c:+6.2f}% {s['signal'].split()[0]}\n"
    msg4 += "\n📉 하락 TOP 8\n\n"
    for s in kr_sorted[-8:][::-1]:
        c = s.get('change_pct') or 0
        msg4 += f"{s['name']:<8}({s['sym']}) {s['price']:>9,.0f}원 {c:+6.2f}% {s['signal'].split()[0]}\n"

    # Msg 5: 미국 등락
    us_sorted = sorted(us, key=lambda x: x.get("change_pct", 0) or 0, reverse=True)
    msg5 = "🇺🇸 미국 시장 등락\n━━━━━━━━━━━━━━━\n📈 상승 TOP 8\n\n"
    for s in us_sorted[:8]:
        c = s.get('change_pct') or 0
        msg5 += f"{s['name']:<8}({s['sym']:<5}) ${s['price']:>8,.2f} {c:+6.2f}% {s['signal'].split()[0]}\n"
    msg5 += "\n📉 하락 TOP 8\n\n"
    for s in us_sorted[-8:][::-1]:
        c = s.get('change_pct') or 0
        msg5 += f"{s['name']:<8}({s['sym']:<5}) ${s['price']:>8,.2f} {c:+6.2f}% {s['signal'].split()[0]}\n"

    # Msg 6: AI 종합
    msg6 = (
        f"🤖 AI 합의 분석 (유효 {valid}/3)\n━━━━━━━━━━━━━━━\n\n{final}\n\n"
        f"━━━━━━━━━━━━━━━\n📡 Yahoo Finance / 🤖 Gemini+OpenRouter\n책임: 본인"
    )

    # Msg 7: 최종 액션 플랜
    msg7 = "🎯 오늘의 최종 액션 플랜\n━━━━━━━━━━━━━━━\n(보유+추천 종합 결정)\n\n"
    add_buy = [s for s in holdings_stocks if "추가 매수" in s.get('action', '')]
    hold = [s for s in holdings_stocks if "보유 유지" in s.get('action', '') or "홀드" in s.get('action', '')]
    take_profit = [s for s in holdings_stocks if "차익실현" in s.get('action', '')]
    sell = [s for s in holdings_stocks if "매도 검토" in s.get('action', '')]

    msg7 += "📌 보유 종목 액션\n\n"
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

    msg7 += "📌 신규 매수 후보 (점수)\n\n"
    msg7 += "🇰🇷 한국 TOP 3\n"
    for i, s in enumerate(kr_top[:3], 1):
        msg7 += f"  {i}. {s['name']}({s['sym']}) 점수 {s['score']}  여력 {s.get('upside') or 0:+.0f}%\n"
    msg7 += "\n🇺🇸 미국 TOP 3\n"
    for i, s in enumerate(us_top[:3], 1):
        msg7 += f"  {i}. {s['name']}({s['sym']}) 점수 {s['score']}  여력 {s.get('upside') or 0:+.0f}%\n"

    msg7 += (
        "\n━━━━━━━━━━━━━━━\n📋 결정 가이드\n"
        "• 위 액션은 시스템 권고\n"
        "• 본인 판단으로 진행/수정/삭제\n"
        "• 최종 매매 책임: 본인"
    )

    msgs = [msg0, msg1, msg2, msg3, msg4, msg5, msg6, msg7]
    for i, m in enumerate(msgs, 1):
        ok = tg_send(m)
        print(f"  Msg {i}/{len(msgs)}: {'OK' if ok else 'FAIL'}")
        time.sleep(1)
    print(f"[완료] {NOW}")


if __name__ == "__main__":
    main()
