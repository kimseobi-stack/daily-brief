"""키움 KH OpenAPI 동기화 모듈 - 모의/실전 잔고 + 시세."""
import os
import requests

APPKEY = os.environ.get("KIWOOM_APPKEY", "")
APPSECRET = os.environ.get("KIWOOM_APPSECRET", "")
ACCOUNT = os.environ.get("KIWOOM_ACCOUNT", "")
ENV = os.environ.get("KIWOOM_BASE", "mock")  # mock | real

BASE = "https://mockapi.kiwoom.com" if ENV == "mock" else "https://api.kiwoom.com"


def get_token():
    """접근 토큰 발급."""
    if not APPKEY or not APPSECRET:
        return None
    try:
        r = requests.post(f"{BASE}/oauth2/token",
            json={"grant_type": "client_credentials",
                  "appkey": APPKEY, "secretkey": APPSECRET},
            headers={"Content-Type": "application/json;charset=UTF-8"},
            timeout=15)
        d = r.json()
        if d.get("return_code") == 0:
            return d.get("token")
    except Exception:
        pass
    return None


def fetch_balance(token):
    """예수금 + 보유종목 조회."""
    if not token:
        return None
    H = {"Content-Type": "application/json;charset=UTF-8",
         "authorization": f"Bearer {token}"}

    out = {"deposit": 0, "holdings": [], "tot_eval": 0, "tot_pl": 0, "tot_pl_pct": 0}

    # 예수금 (kt00001)
    try:
        r = requests.post(f"{BASE}/api/dostk/acnt",
            headers={**H, "api-id": "kt00001"},
            json={"qry_tp": "3"}, timeout=15)
        d = r.json()
        if d.get("return_code") == 0:
            out["deposit"] = int(d.get("entr", "0"))
    except Exception:
        pass

    # 잔고내역 (kt00018) - 보유 종목 + 평가
    try:
        r = requests.post(f"{BASE}/api/dostk/acnt",
            headers={**H, "api-id": "kt00018"},
            json={"qry_tp": "1", "dmst_stex_tp": "KRX"}, timeout=15)
        d = r.json()
        if d.get("return_code") == 0:
            out["tot_eval"] = int(d.get("tot_evlt_amt", "0"))
            out["tot_pl"] = int(d.get("tot_evlt_pl", "0"))
            out["tot_pl_pct"] = float(d.get("tot_prft_rt", "0"))
            # 보유 종목 리스트
            for item in d.get("acnt_evlt_remn_indv_tot", []):
                out["holdings"].append({
                    "sym": str(item.get("stk_cd", "")).strip().lstrip("A"),
                    "name": str(item.get("stk_nm", "")).strip(),
                    "qty": int(item.get("rmnd_qty", "0")),
                    "avg_price": int(item.get("pur_pric", "0")),
                    "cur_price": int(item.get("cur_prc", "0")),
                    "eval_amt": int(item.get("evlt_amt", "0")),
                    "pl_amt": int(item.get("evltv_prft", "0")),
                    "pl_pct": float(item.get("prft_rt", "0")),
                })
    except Exception:
        pass

    return out


def fetch_kr_quote(token, symbol):
    """한국 종목 실시간 시세 (키움 ka10095 시세표성정보요청 또는 ka10001)."""
    if not token:
        return None
    H = {"Content-Type": "application/json;charset=UTF-8",
         "authorization": f"Bearer {token}",
         "api-id": "ka10001"}
    try:
        r = requests.post(f"{BASE}/api/dostk/stkinfo",
            headers=H, json={"stk_cd": symbol}, timeout=15)
        d = r.json()
        if d.get("return_code") == 0:
            cur = abs(int(d.get("cur_prc", "0")))
            prev = int(d.get("base_pric", "0"))  # 기준가 (전일 종가)
            chg = (cur / prev - 1) * 100 if prev else None
            return {"price": cur, "prev": prev, "change_pct": chg}
    except Exception:
        pass
    return None


if __name__ == "__main__":
    token = get_token()
    print(f"Token: {'OK' if token else 'FAIL'}")
    if token:
        bal = fetch_balance(token)
        print(f"예수금: {bal['deposit']:,}원")
        print(f"평가금액: {bal['tot_eval']:,}원")
        print(f"보유 종목: {len(bal['holdings'])}개")
        for h in bal['holdings']:
            print(f"  {h['name']}({h['sym']}) {h['qty']}주 평균{h['avg_price']:,}원 현재{h['cur_price']:,}원 손익{h['pl_pct']:+.2f}%")
        # 시세 테스트
        q = fetch_kr_quote(token, "005930")
        if q:
            print(f"삼성전자 실시간: {q['price']:,}원 ({q['change_pct']:+.2f}%)")
