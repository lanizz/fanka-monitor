#!/usr/bin/env python3
"""饭卡余额 + 消费明细查询（完美学堂/新中新 17wanxiao ecard H5 API）。

用法：
  python3 fanka.py balance            # 余额 + 最近 3 天消费明细（每日推送用）
  python3 fanka.py check              # 静默检查：余额低于阈值才告警（每 2 小时跑），
                                      #   同类告警每天最多一次；余额恢复后自动重置
  python3 fanka.py check --threshold 30

机制：每次运行都用 data/config.json 的账号密码直登 open.17wanxiao.com
换取全新会话（authorize 建 SESSION → POST /api/login → authorize 302 带 code
→ bootcallback 签发会话），再查余额/明细。不依赖任何长寿 token，
手机上打开小程序踢掉旧 token 也不影响。明细累积归档在 data/transactions.json。

配置：复制 config.example.json 为 data/config.json 并填写，各字段获取方法见 README.md。
"""
import argparse
import datetime
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from http.cookies import SimpleCookie
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "data" / "config.json"
COOKIES_PATH = BASE_DIR / "data" / "cookies.json"
STATE_PATH = BASE_DIR / "data" / "alert_state.json"
TXN_PATH = BASE_DIR / "data" / "transactions.json"

API = "https://ecardh5.17wanxiao.com/ecardh5/bootcallback"
OPEN_BASE = "https://open.17wanxiao.com"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/154.0.0.0 Safari/537.36")

REQUIRED_KEYS = ("username", "password", "customerCode", "outid",
                 "openid", "client_id", "flag")

DEFAULT_THRESHOLD = 30.0
RECENT_DAYS = 3
TXN_PAGE_SIZE = 50


class AuthError(Exception):
    """登录失败或会话异常。"""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """不自动跟随 302（要亲手拿 Location 和 Set-Cookie）。"""

    def redirect_request(self, *args, **kwargs):
        return None


def load_config() -> dict:
    try:
        cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except OSError:
        raise AuthError(f"缺少配置 {CONFIG_PATH}：请复制 config.example.json 填写")
    missing = [k for k in REQUIRED_KEYS if not cfg.get(k)]
    if missing:
        raise AuthError(f"配置缺字段 {missing}：请参照 config.example.json 补齐")
    return cfg


def build_authorize(cfg: dict) -> str:
    """授权页 URL。query 串里的 client_id 等参数一个都不能少（否则 bad client_id）。"""
    inner = ("https://ecardh5.17wanxiao.com/ecardh5/bootcallback?ecardFunc=index"
             f"&flag={cfg['flag']}&openid={cfg['openid']}&appId={cfg['client_id']}"
             f"&aliUid={cfg['openid']}&customer_code={cfg['customerCode']}")
    return (f"{OPEN_BASE}/api/authorize"
            "?response_type=code&hidden=true&force_login=false"
            f"&customerCode={cfg['customerCode']}"
            f"&redirect_uri={urllib.parse.quote(inner, safe='')}"
            f"&client_id={cfg['client_id']}")


def cookie_header(cookies: dict) -> str:
    return "; ".join(f"{k}={v}" for k, v in cookies.items())


def merge_set_cookie(headers, cookies: dict) -> None:
    for sc in headers.get_all("Set-Cookie") or []:
        jar = SimpleCookie()
        jar.load(sc)
        for name, morsel in jar.items():
            cookies[name] = morsel.value


def password_login(cfg: dict) -> dict:
    """账号密码直登 open.17wanxiao.com，返回全套新会话 cookie。失败抛 AuthError。"""
    cookies: dict = {}
    authorize = build_authorize(cfg)
    opener = urllib.request.build_opener(_NoRedirect)

    def step(url: str, data: bytes | None = None,
             extra: dict | None = None) -> tuple[int, object, bytes]:
        headers = {"User-Agent": UA, "Cookie": cookie_header(cookies)}
        if extra:
            headers.update(extra)
        req = urllib.request.Request(url, data=data, headers=headers)
        try:
            resp = opener.open(req, timeout=15)
            status, hdrs, body = resp.status, resp.headers, resp.read()
        except urllib.error.HTTPError as e:
            status, hdrs, body = e.code, e.headers, e.read()
        merge_set_cookie(hdrs, cookies)
        return status, hdrs, body

    step(authorize)  # 1) 建立登录 SESSION
    form = urllib.parse.urlencode({
        "loginType": "ecard",
        "username": cfg["username"],
        "password": cfg["password"],
        "customerCode": cfg["customerCode"],
        "outid": cfg["outid"],
        "scopes[]": ["base", "ecard_info", "user_alter", "ecard_alter",
                     "vcard_alert", "vcard_info", "vcard_pay"],
        "force_bind_ecard": "true",
    }, doseq=True).encode()
    _, _, body = step(  # 2) 提交账号密码
        authorize.replace("/api/authorize", "/api/login"), data=form,
        extra={"Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
               "Accept": "application/json, text/javascript, */*; q=0.01",
               "Origin": OPEN_BASE,
               "Referer": authorize,
               "X-Requested-With": "XMLHttpRequest"})
    try:
        ok = json.loads(body).get("errorCode") == 0
    except json.JSONDecodeError:
        ok = False
    if not ok:
        raise AuthError(f"密码登录失败（卡密码可能已改，检查 config.json）：{body[:200]!r}")
    status, hdrs, _ = step(authorize)  # 3) 已登录，授权 302 带 code
    loc = hdrs.get("Location", "")
    if status not in (301, 302, 303, 307) or "bootcallback" not in loc:
        raise AuthError(f"登录后未获得授权跳转：HTTP {status}")
    step(urllib.parse.urljoin(authorize, loc))  # 4) bootcallback 签发会话
    if "ECARD_H5_SESSION" not in cookies:
        raise AuthError("登录成功但未获得会话")
    return cookies


def api_post(cookies: dict, data: str) -> dict:
    """POST bootcallback，返回 data 字段。失败抛 AuthError/异常。"""
    req = urllib.request.Request(API, data=data.encode(), headers={
        "User-Agent": UA,
        "Cookie": cookie_header(cookies),
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
        "Origin": "https://ecardh5.17wanxiao.com",
        "Referer": "https://ecardh5.17wanxiao.com/ecardh5/ecard_t/index.html?v=1",
    })
    with urllib.request.urlopen(req, timeout=15) as resp:
        raw = resp.read()
        merge_set_cookie(resp.headers, cookies)
    try:
        payload = json.loads(raw.decode("utf-8", errors="replace"))
    except json.JSONDecodeError:
        raise AuthError(f"响应不是 JSON（会话异常）：{raw[:200]!r}")
    if not payload.get("result_") or payload.get("code_") != 0:
        raise AuthError(f"API 返回异常（会话异常）：{raw[:200]!r}")
    return payload["data"]


def query_balance(cookies: dict) -> dict:
    return api_post(cookies, "gotowhere=XYK_BASE_INFO")


def query_transactions(cookies: dict, days: int = RECENT_DAYS) -> list:
    """最近 N 天（含今天）交易明细，新→旧由调用方排序。"""
    today = datetime.date.today()
    begin = today - datetime.timedelta(days=days - 1)
    data = api_post(cookies, urllib.parse.urlencode({
        "beginIndex": 0, "pageSize": TXN_PAGE_SIZE, "type": -1,
        "beginDate": begin.isoformat(), "endDate": today.isoformat(),
        "gotowhere": "XYK_TRADE_DETAIL",
    }))
    return data.get("data") or []


def save_cookies(cookies: dict) -> None:
    COOKIES_PATH.write_text(json.dumps(cookies, ensure_ascii=False, indent=2),
                            encoding="utf-8")


def save_transactions(records: list) -> None:
    """按 time（毫秒时间戳）去重，累积归档全部历史明细。"""
    try:
        archive = json.loads(TXN_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        archive = {}
    for r in records:
        if r.get("time"):
            archive[str(r["time"])] = r
    TXN_PATH.write_text(json.dumps(archive, ensure_ascii=False, indent=2),
                        encoding="utf-8")


EXPENSE_WORDS = ("支出", "消费", "扣")
INCOME_WORDS = ("充值", "收入", "退款", "入账", "补助")


def fmt_amount(r: dict) -> str:
    text = f"{r.get('accdscrp', '')}{r.get('description', '')}"
    amount = float(r.get("amount", 0))
    if any(w in text for w in INCOME_WORDS):
        return f"+{amount:.2f}"
    if any(w in text for w in EXPENSE_WORDS):
        return f"-{amount:.2f}"
    return f"{amount:.2f}"


def fmt_transactions(records: list) -> str:
    if not records:
        return "📋 最近 3 天无消费记录"
    lines = ["📋 最近 3 天消费："]
    for r in sorted(records, key=lambda x: x.get("time", 0), reverse=True):
        dt = str(r.get("businessopdt", ""))[5:16]  # "2026-10-15 12:42:02" → "10-15 12:42"
        lines.append(f"{dt}  {r.get('term_name', '?')}  {fmt_amount(r)}")
    return "\n".join(lines)


def fmt_balance(d: dict) -> str:
    lines = [f"🍚 饭卡余额：{d['my_fare']} 元"]
    if d.get("subsidy_fare"):
        lines.append(f"   补助钱包：{d['subsidy_fare']:.2f} 元")
    return "\n".join(lines)


def load_state() -> dict:
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def save_state(state: dict) -> None:
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")


def already_alerted(key: str) -> bool:
    """check 模式告警去重：同类告警每天最多一次。首次告警会记录今天。"""
    today = datetime.date.today().isoformat()
    state = load_state()
    if state.get(key) == today:
        return True
    state[key] = today
    save_state(state)
    return False


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["balance", "check"])
    ap.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    args = ap.parse_args()

    try:
        cfg = load_config()
        cookies = password_login(cfg)
        save_cookies(cookies)  # 留存便于调试，不依赖其有效性
        data = query_balance(cookies)
    except AuthError as e:
        if args.command == "check" and already_alerted("auth_alert"):
            sys.exit(1)  # 失败也每天只提醒一次，避免每 2 小时刷屏
        print(f"⚠️ 饭卡查询失败：{e}")
        sys.exit(1)
    except Exception as e:
        print(f"⚠️ 饭卡查询出错：{type(e).__name__}: {e}")
        sys.exit(1)

    balance = float(data["main_fare"])
    if args.command == "balance":
        print(fmt_balance(data))
        if balance < args.threshold:
            print(f"⚠️ 已低于 {args.threshold:.0f} 元，记得充值！")
        try:
            txns = query_transactions(cookies)
            save_transactions(txns)
            print()
            print(fmt_transactions(txns))
        except Exception as e:
            print(f"\n（消费明细查询失败，不影响余额：{e}）")
    else:  # check：静默模式 + 每天最多告警一次（充值后需实体卡圈存，余额不会马上涨）
        if balance < args.threshold:
            if not already_alerted("low_alert"):
                print(f"🚨 饭卡余额不足！当前 {data['my_fare']} 元 "
                      f"（低于 {args.threshold:.0f} 元），请尽快充值！")
        else:
            state = load_state()
            if state.pop("low_alert", None):
                save_state(state)  # 余额已恢复，重置以便下次跌落再告警


if __name__ == "__main__":
    main()
