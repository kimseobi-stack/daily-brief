"""Auto Trade v2 - 모의 한정 공격형, 점수 70+ 매수, 한국 종목만 매매."""
import os, time, json, requests
from datetime import datetime
from zoneinfo import ZoneInfo
import yfinance as yf
import pandas as pd
import kiwoom_sync as kw

if os.environ.get("KIWOOM_BASE", "mock") != "mock":
    raise SystemExit("AUTO_TRADE: 실전 차단")

KST = ZoneInfo("Asia/Seoul")
NOW = datetime.now(KST)
HOUR = NOW.hour
MIN = NOW.minute
NOW_STR = NOW.strftime("%Y-%m-%d %H:%M KST")

in_market = (HOUR == 9 and MIN >= 0) or (10 <= HOUR <= 14) or (HOUR == 15 and MIN <= 20)
if not in_market:
    print(f"[{NOW_STR}] 장 시간 외, 종료")
    raise SystemExit(0)

TG_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TG_CHAT = os.environ["TELEGRAM_CHAT_ID"]
BASE = "https://mockapi.kiwoom.com"
UA = {"User-Agent": "Mozilla/5.0"}

PER_STOCK_KRW = 150_000_000
MAX_HOLDINGS = 5
MAX_NEW_PER_DAY = 5
STOP_LOSS_PCT = -7.0
PARTIAL_TAKE_1 = 15.0
PARTIAL_TAKE_2 = 25.0
MIN_SCORE = 70  # v2: 65→70 품질 우선
VIX_BLOCK = 30.0

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
    try:
        requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={"chat_id": TG_CHAT, "text": msg[:4000],
                  "disable_web_page_preview": True}, timeout=15)
    except: pass


def vix_check():
    try:
        url = "https://query1.finance.yahoo.com/v8/finance/chart/^VIX?interval=1d&range=2d"
        r = requests.get(url, headers=UA, timeout=10)
        v = r.json()["chart"]["result"][0]["meta"].get("regularMarketPrice", 0)
        return v, v < VIX_BLOCK
    except: return None, True


def fundamental_score(symbol, price, chg):
    """v9.2 점수 체계 동일: ROE 20 + 가치 20 + 추세 25 + 컨센 20 + 모멘텀 15."""
    try:
        info = yf.Ticker(f"{symbol}.KS").info
        roe = info.get("returnOnEquity") or 0
        pe = info.get("trailingPE")
        pb = info.get("priceToBook")
        psr = info.get("priceToSalesTrailing12Months")
        peg = info.get("pegRatio") or info.get("trailingPegRatio")
        debt = info.get("debtToEquity")
        eps_g = info.get("earningsQuarterlyGrowth") or 0
        target = info.get("targetMeanPrice")
        rec = info.get("recommendationKey") or "hold"
        ma50 = info.get("fiftyDayAverage")
        ma200 = info.get("twoHundredDayAverage")
        above_50 = bool(ma50 and price > ma50)
        above_200 = bool(ma200 and price > ma200)
        upside = (target / price - 1) * 100 if target else 0

        score = 0
        score += min(max(roe * 100, 0) / 25 * 20, 20)
        pb_ok = bool(pb and 0 < pb < 1)
        psr_ok = bool(psr and 0 < psr < 1)
        peg_ok = bool(peg and 0 < peg < 1)
        debt_ok = bool(debt is not None and debt < 100)
        score += sum([5 if x else 0 for x in [pb_ok, psr_ok, peg_ok, debt_ok]])
        if above_50: score += 8
        if above_200: score += 10
        score += {"strong_buy":10,"buy":8,"hold":5,"sell":2,"strong_sell":0}.get(rec, 5)
        score += min(max(upside, 0) / 30 * 10, 10)
        score += min(max(eps_g * 100, 0) / 50 * 5, 5)
        if chg > 0: score += min(chg / 5 * 10, 10)
        elif chg > -2: score += 3
        return round(min(100, max(0, score)), 1), above_200, debt, roe
    except Exception:
        return 0, False, None, None


def order(token, sym, qty, side):
    api_id = "kt10000" if side == "buy" else "kt10001"
    H = {"Content-Type": "application/json;charset=UTF-8",
         "authorization": f"Bearer {token}", "api-id": api_id}
    body = {"dmst_stex_tp": "KRX", "stk_cd": sym, "ord_qty": str(qty),
            "ord_uv": "", "trde_tp": "3", "cond_uv": ""}
    try:
        r = requests.post(f"{BASE}/api/dostk/ordr", headers=H, json=body, timeout=15)
        d = r.json()
        return d.get("return_code") == 0, d
    except Exception as e:
        return False, {"error": str(e)}


def main():
    log = [f"🤖 Auto Trade v2 ({NOW_STR})\n━━━━━━━━━━━━━"]
    actions = []
    token = kw.get_token()
    if not token:
        tg("⚠️ Auto Trade: 토큰 실패"); return

    vix, vix_ok = vix_check()
    log.append(f"VIX {vix:.1f} {'OK' if vix_ok else '차단'}" if vix else "VIX 조회실패")

    bal = kw.fetch_balance(token)
    if not bal:
        tg("⚠️ 잔고 실패"); return
    deposit = bal["deposit"]
    holdings = bal["holdings"]
    held_syms = {h["sym"] for h in holdings}
    log.append(f"예수금 {deposit:,}원 | 보유 {len(holdings)}/{MAX_HOLDINGS}")

    # 매도 (손절/익절)
    for h in holdings:
        pl = h["pl_pct"]
        if pl <= STOP_LOSS_PCT:
            ok, _ = order(token, h["sym"], h["qty"], "sell")
            actions.append(f"🔴손절 {h['name']}({h['sym']}) {h['qty']}주 ({pl:+.2f}%) {'OK' if ok else 'FAIL'}")
            continue
        if pl >= PARTIAL_TAKE_2 and h["qty"] >= 2:
            q = h["qty"] // 2
            ok, _ = order(token, h["sym"], q, "sell")
            actions.append(f"🟢2차익절 {h['name']} {q}주 ({pl:+.2f}%) {'OK' if ok else 'FAIL'}")
            continue
        if pl >= PARTIAL_TAKE_1 and h["qty"] >= 3:
            q = h["qty"] // 3
            ok, _ = order(token, h["sym"], q, "sell")
            actions.append(f"🟢1차익절 {h['name']} {q}주 ({pl:+.2f}%) {'OK' if ok else 'FAIL'}")

    # 매수
    if vix_ok and len(holdings) < MAX_HOLDINGS and deposit >= PER_STOCK_KRW:
        candidates = []
        for sym, name in KR_POOL:
            if sym in held_syms: continue
            q = kw.fetch_kr_quote(token, sym)
            if not q or not q.get("price"): continue
            price = q["price"]
            chg = q.get("change_pct") or 0
            score, above_200, debt, roe = fundamental_score(sym, price, chg)
            if score < MIN_SCORE: continue
            if not above_200: continue
            if (roe or 0) * 100 < 10: continue
            if (debt or 999) >= 200: continue
            candidates.append({"sym": sym, "name": name, "price": price,
                               "chg": chg, "score": score, "roe": roe})
            time.sleep(0.2)

        candidates.sort(key=lambda x: x["score"], reverse=True)
        slots = MAX_HOLDINGS - len(holdings)
        bought = 0
        for c in candidates[:slots]:
            if deposit < PER_STOCK_KRW: break
            qty = int(PER_STOCK_KRW // c["price"])
            if qty < 1: continue
            ok, _ = order(token, c["sym"], qty, "buy")
            if ok:
                deposit -= qty * c["price"]
                bought += 1
                actions.append(f"🔥신규매수 {c['name']}({c['sym']}) {qty}주 @ {c['price']:,}원 "
                               f"점수{c['score']:.0f} ROE{(c['roe'] or 0)*100:.1f}% OK")
            else:
                actions.append(f"FAIL 매수실패 {c['name']}({c['sym']})")
            time.sleep(0.5)
            if bought >= MAX_NEW_PER_DAY: break

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
