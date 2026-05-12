"""
Auto Trade v1 - 모의 한정 공격형 자동매매
- 종목당 1.5억 (30%), 동시 3~5종목, 1일 5건 신규
- 손절 -7%, 익절 +15% 1/3, +25% 1/2
- 점수 65+ & 30주선 위 & VIX 30 미만
- 5분 간격 실행 (장중 KST 09:00~15:30)
- 안전장치: KIWOOM_BASE != 'mock' 이면 즉시 종료
"""
import os, time, json, requests
from datetime import datetime
from zoneinfo import ZoneInfo
import yfinance as yf
import pandas as pd
import kiwoom_sync as kw

# ===== 안전장치: 모의만 =====
if os.environ.get("KIWOOM_BASE", "mock") != "mock":
    raise SystemExit("AUTO_TRADE: 실전 환경 차단. KIWOOM_BASE=mock 필수.")

KST = ZoneInfo("Asia/Seoul")
NOW = datetime.now(KST)
HOUR = NOW.hour
MIN = NOW.minute
NOW_STR = NOW.strftime("%Y-%m-%d %H:%M KST")

# ===== 장 시간 체크 (KST 09:00~15:20) =====
in_market = (HOUR == 9 and MIN >= 0) or (10 <= HOUR <= 14) or (HOUR == 15 and MIN <= 20)
if not in_market:
    print(f"[{NOW_STR}] 장 시간 외, 종료")
    raise SystemExit(0)

TG_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TG_CHAT = os.environ["TELEGRAM_CHAT_ID"]
BASE = "https://mockapi.kiwoom.com"
UA = {"User-Agent": "Mozilla/5.0"}

# ===== 공격형 룰 =====
PER_STOCK_KRW = 150_000_000   # 종목당 1.5억
MAX_HOLDINGS = 5              # 동시 보유 최대
MAX_NEW_PER_DAY = 5           # 1일 신규 매수
STOP_LOSS_PCT = -7.0          # 손절선
PARTIAL_TAKE_1 = 15.0         # 1차 익절 (1/3)
PARTIAL_TAKE_2 = 25.0         # 2차 익절 (1/2)
MIN_SCORE = 65                # 매수 점수 기준
VIX_BLOCK = 30.0              # VIX 차단 임계값

# 후보 종목 풀 (한국 주요)
KR_POOL = [
    ("005930", "삼성전자"), ("000660", "SK하이닉스"),
    ("373220", "LG에너지"), ("267260", "HD현대일렉트릭"),
    ("329180", "HD현대중공업"), ("042660", "한화오션"),
    ("010140", "삼성중공업"), ("298040", "효성중공업"),
    ("010120", "LS일렉트릭"), ("207940", "삼성바이오"),
    ("068270", "셀트리온"), ("005490", "POSCO홀딩스"),
    ("086520", "에코프로"), ("247540", "에코프로비엠"),
    ("005380", "현대차"), ("105560", "KB금융"),
]


def tg(msg):
    """텔레그램 발송."""
    try:
        requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={"chat_id": TG_CHAT, "text": msg[:4000],
                  "disable_web_page_preview": True}, timeout=15)
    except Exception:
        pass


def vix_check():
    """VIX 30 이상이면 차단."""
    try:
        url = "https://query1.finance.yahoo.com/v8/finance/chart/^VIX?interval=1d&range=2d"
        r = requests.get(url, headers=UA, timeout=10)
        v = r.json()["chart"]["result"][0]["meta"].get("regularMarketPrice", 0)
        return v, v < VIX_BLOCK
    except Exception:
        return None, True  # 조회 실패 시 통과


def weekly_above_30(symbol):
    """30주선 위 여부 (야후 .KS)."""
    try:
        df = yf.download(f"{symbol}.KS", period="2y", interval="1wk",
                         progress=False, auto_adjust=False, threads=False)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = [c[0] for c in df.columns]
        if df.empty or len(df) < 30:
            return False
        c = df["Close"]
        ma30 = c.rolling(30).mean().iloc[-1]
        return float(c.iloc[-1]) > float(ma30)
    except Exception:
        return False


def fundamental_score(symbol, price, change_pct):
    """daily_brief.py 점수 로직 단순화."""
    try:
        t = yf.Ticker(f"{symbol}.KS")
        info = t.info
        peg = info.get("pegRatio")
        eps_g = (info.get("earningsQuarterlyGrowth") or 0) * 100
        target = info.get("targetMeanPrice")
        rec = info.get("recommendationKey") or ""
        analysts = info.get("numberOfAnalystOpinions") or 0
        upside = (target / price - 1) * 100 if target and price else 0

        score = 0
        score += min(max(upside, 0) / 30 * 25, 25)
        score += min(max(eps_g, 0) / 50 * 20, 20)
        if peg and peg > 0:
            score += min(max(2 - peg, 0) / 2 * 10, 10)
        score += {"strong_buy": 10, "buy": 8, "hold": 4,
                  "sell": 1, "strong_sell": 0}.get(rec, 4)
        if change_pct > 0:
            score += min(change_pct / 5 * 15, 15)
        elif change_pct > -2:
            score += 5
        score += min(analysts / 30 * 10, 10) + 5
        return round(min(100, max(0, score)), 1)
    except Exception:
        return 0


def order(token, sym, qty, side, env_label="모의"):
    """주문 실행. side: 'buy' or 'sell'. 시장가."""
    api_id = "kt10000" if side == "buy" else "kt10001"
    H = {"Content-Type": "application/json;charset=UTF-8",
         "authorization": f"Bearer {token}",
         "api-id": api_id}
    body = {
        "dmst_stex_tp": "KRX",
        "stk_cd": sym,
        "ord_qty": str(qty),
        "ord_uv": "",
        "trde_tp": "3",  # 시장가
        "cond_uv": "",
    }
    try:
        r = requests.post(f"{BASE}/api/dostk/ordr",
                          headers=H, json=body, timeout=15)
        d = r.json()
        return d.get("return_code") == 0, d
    except Exception as e:
        return False, {"error": str(e)}


def main():
    log = [f"🤖 Auto Trade ({NOW_STR})\n━━━━━━━━━━━━━"]
    actions = []

    # 1. 토큰
    token = kw.get_token()
    if not token:
        tg("⚠️ Auto Trade: 키움 토큰 발급 실패")
        return

    # 2. VIX 체크
    vix, vix_ok = vix_check()
    log.append(f"VIX: {vix:.2f} {'✅' if vix_ok else '🚫차단'}" if vix else "VIX 조회실패")

    # 3. 잔고
    bal = kw.fetch_balance(token)
    if not bal:
        tg("⚠️ Auto Trade: 잔고 조회 실패")
        return

    deposit = bal["deposit"]
    holdings = bal["holdings"]
    holding_syms = {h["sym"] for h in holdings}
    log.append(f"예수금: {deposit:,}원 | 보유: {len(holdings)}/{MAX_HOLDINGS}종목")

    # 4. 매도 체크 (보유 종목 손절/익절)
    for h in holdings:
        pl_pct = h["pl_pct"]
        sym = h["sym"]
        name = h["name"]
        qty = h["qty"]

        # 손절
        if pl_pct <= STOP_LOSS_PCT:
            ok, _ = order(token, sym, qty, "sell")
            actions.append(f"🔴손절 {name}({sym}) {qty}주 ({pl_pct:+.2f}%) {'✅' if ok else '❌'}")
            continue

        # 2차 익절 (1/2)
        if pl_pct >= PARTIAL_TAKE_2 and qty >= 2:
            sell_qty = qty // 2
            ok, _ = order(token, sym, sell_qty, "sell")
            actions.append(f"🟢2차익절 {name}({sym}) {sell_qty}주 ({pl_pct:+.2f}%) {'✅' if ok else '❌'}")
            continue

        # 1차 익절 (1/3)
        if pl_pct >= PARTIAL_TAKE_1 and qty >= 3:
            sell_qty = qty // 3
            ok, _ = order(token, sym, sell_qty, "sell")
            actions.append(f"🟢1차익절 {name}({sym}) {sell_qty}주 ({pl_pct:+.2f}%) {'✅' if ok else '❌'}")

    # 5. 매수 체크 (VIX OK + 보유 5종목 미만 + 예수금 충분)
    if vix_ok and len(holdings) < MAX_HOLDINGS and deposit >= PER_STOCK_KRW:
        candidates = []
        for sym, name in KR_POOL:
            if sym in holding_syms:
                continue
            q = kw.fetch_kr_quote(token, sym)
            if not q or not q.get("price"):
                continue
            price = q["price"]
            chg = q.get("change_pct") or 0
            score = fundamental_score(sym, price, chg)
            if score < MIN_SCORE:
                continue
            if not weekly_above_30(sym):
                continue
            candidates.append({"sym": sym, "name": name, "price": price,
                               "chg": chg, "score": score})
            time.sleep(0.2)

        candidates.sort(key=lambda x: x["score"], reverse=True)

        slots = MAX_HOLDINGS - len(holdings)
        bought = 0
        for c in candidates[:slots]:
            if deposit < PER_STOCK_KRW:
                break
            qty = int(PER_STOCK_KRW // c["price"])
            if qty < 1:
                continue
            ok, _ = order(token, c["sym"], qty, "buy")
            if ok:
                deposit -= qty * c["price"]
                bought += 1
                actions.append(
                    f"🔥신규매수 {c['name']}({c['sym']}) {qty}주 "
                    f"@ {c['price']:,}원 (점수 {c['score']}) ✅")
            else:
                actions.append(f"❌매수실패 {c['name']}({c['sym']})")
            time.sleep(0.5)
            if bought >= MAX_NEW_PER_DAY:
                break

    # 6. 알림 (실행 내역 있을 때만)
    if actions:
        msg = "\n".join(log) + "\n\n" + "\n".join(actions)
        msg += f"\n\n잔여 예수금: {deposit:,}원"
        tg(msg)
        print(msg)
    else:
        print("\n".join(log))
        print("\n  매매 없음")


if __name__ == "__main__":
    main()
