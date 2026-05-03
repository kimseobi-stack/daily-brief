"""
Alert: 긴급 매수/매도 시그널 모니터링
- 장중 5분 간격 실행
- 보유 종목 + 시장 지표 트리거 체크
- 조건 충족 시에만 텔레그램 발송 (평소 침묵)
"""
import os
import json
import requests
import yfinance as yf
from datetime import datetime
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")
NOW = datetime.now(KST)
NOW_STR = NOW.strftime("%H:%M KST")

TG_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TG_CHAT = os.environ["TELEGRAM_CHAT_ID"]
HOLDINGS_JSON = os.environ.get("HOLDINGS_JSON", "[]")

UA = {"User-Agent": "Mozilla/5.0"}

# 보유 종목 (Secret에서 로드)
HOLDINGS = json.loads(HOLDINGS_JSON)


# ============================================================
# 시세 + 변동 체크
# ============================================================
def yf_quote(symbol):
    try:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=5m&range=1d"
        r = requests.get(url, headers=UA, timeout=10)
        d = r.json()["chart"]["result"][0]
        meta = d["meta"]
        ind = d.get("indicators", {}).get("quote", [{}])[0]
        vols = [v for v in ind.get("volume", []) if v]

        # 정보 수집
        return {
            "price": meta.get("regularMarketPrice"),
            "prev_close": meta.get("chartPreviousClose"),
            "high": meta.get("regularMarketDayHigh"),
            "low": meta.get("regularMarketDayLow"),
            "volume": sum(vols) if vols else 0,
            "52w_high": meta.get("fiftyTwoWeekHigh"),
            "52w_low": meta.get("fiftyTwoWeekLow"),
            "currency": meta.get("currency"),
        }
    except Exception:
        return None


def check_triggers(s):
    """단일 종목에 대한 트리거 체크. 트리거 리스트 반환."""
    triggers = []
    q = yf_quote(s["sym"])
    if not q or not q.get("price"):
        return triggers

    price = q["price"]
    prev = q["prev_close"]
    chg_pct = (price / prev - 1) * 100 if prev else 0

    # 보유 종목인지
    is_holding = s.get("is_holding", False)
    threshold_change = 3.0 if is_holding else 5.0  # 보유는 더 민감

    # 1. 큰 변동
    if abs(chg_pct) >= threshold_change:
        emoji = "🚀" if chg_pct > 0 else "🔻"
        triggers.append({
            "level": "급변",
            "msg": f"{emoji} {s['name']}({s['sym']}) {chg_pct:+.2f}% | 현재 {price:,.2f} (전일 {prev:,.2f})",
            "score": abs(chg_pct),
        })

    # 2. 52주 신고가
    if q.get("52w_high") and price >= q["52w_high"] * 0.999:
        triggers.append({
            "level": "신고가",
            "msg": f"📈 {s['name']}({s['sym']}) 52주 신고가 돌파 | 현재 {price:,.2f}",
            "score": 80,
        })

    # 3. 52주 신저가
    if q.get("52w_low") and price <= q["52w_low"] * 1.001:
        triggers.append({
            "level": "신저가",
            "msg": f"📉 {s['name']}({s['sym']}) 52주 신저가 | 현재 {price:,.2f}",
            "score": 80,
        })

    # 4. 일중 변동 폭 (high vs low)
    if q.get("high") and q.get("low") and prev:
        intraday_range = (q["high"] - q["low"]) / prev * 100
        if intraday_range >= 8:  # 8% 이상 변동
            triggers.append({
                "level": "변동확대",
                "msg": f"⚠️ {s['name']}({s['sym']}) 일중 변동폭 {intraday_range:.1f}% | 고{q['high']:,.2f}/저{q['low']:,.2f}",
                "score": intraday_range,
            })

    return triggers


def check_macro():
    """매크로 지표 트리거."""
    triggers = []
    targets = [
        ("^KS11", "KOSPI", 2.0),
        ("^GSPC", "SP500", 1.5),
        ("^IXIC", "NASDAQ", 2.0),
        ("KRW=X", "USD/KRW", 1.0),
        ("^VIX", "VIX", None),  # 절대값 25 이상
        ("CL=F", "WTI", 5.0),
        ("BTC-USD", "BTC", 5.0),
    ]
    for sym, name, threshold in targets:
        q = yf_quote(sym)
        if not q or not q.get("price"):
            continue
        price = q["price"]
        prev = q["prev_close"]
        chg = (price / prev - 1) * 100 if prev else 0

        # VIX는 절대값
        if name == "VIX":
            if price >= 25:
                triggers.append({
                    "level": "공포",
                    "msg": f"😱 VIX {price:.2f} - 시장 공포 진입 (전일 {prev:.2f})",
                    "score": price,
                })
        elif threshold and abs(chg) >= threshold:
            emoji = "📈" if chg > 0 else "📉"
            triggers.append({
                "level": "지수경고",
                "msg": f"{emoji} {name} {chg:+.2f}% | 현재 {price:,.2f}",
                "score": abs(chg),
            })

    return triggers


def main():
    print(f"[{NOW.strftime('%Y-%m-%d %H:%M:%S')}] Alert 체크 시작")

    # 보유 종목 + 관심 종목
    watchlist = []
    for h in HOLDINGS:
        watchlist.append({"sym": h["sym"], "name": h["name"], "is_holding": True})

    # 추가 관심 종목 (한국 + 미국 핵심)
    extras = [
        ("005930.KS", "삼성전자"), ("000660.KS", "SK하이닉스"),
        ("373220.KS", "LG에너지솔루션"), ("267260.KS", "HD현대일렉트릭"),
        ("NVDA", "엔비디아"), ("TSLA", "테슬라"), ("AAPL", "애플"),
        ("PLTR", "팔란티어"),
    ]
    held_syms = {h["sym"] for h in HOLDINGS}
    for sym, name in extras:
        if sym not in held_syms:
            watchlist.append({"sym": sym, "name": name, "is_holding": False})

    print(f"  관심: {len(watchlist)}개 (보유 {len(HOLDINGS)}, 추가 {len(extras)})")

    # 트리거 수집
    all_triggers = []
    for s in watchlist:
        all_triggers.extend(check_triggers(s))

    # 매크로
    all_triggers.extend(check_macro())

    print(f"  트리거 발견: {len(all_triggers)}건")

    if not all_triggers:
        print("  조건 미충족, 발송 생략")
        return

    # 점수순 정렬
    all_triggers.sort(key=lambda x: x.get("score", 0), reverse=True)

    # 메시지 작성
    msg = f"🚨 긴급 알림 ({NOW_STR})\n━━━━━━━━━━━━━━━\n\n"
    for t in all_triggers[:15]:  # 최대 15개
        msg += f"[{t['level']}] {t['msg']}\n\n"

    msg += f"━━━━━━━━━━━━━━━\n총 {len(all_triggers)}건 감지\n책임: 본인"

    # 텔레그램 발송
    r = requests.post(
        f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
        json={"chat_id": TG_CHAT, "text": msg[:4000], "disable_web_page_preview": True},
        timeout=15
    )
    print(f"  발송: {r.json().get('ok')}")


if __name__ == "__main__":
    main()
