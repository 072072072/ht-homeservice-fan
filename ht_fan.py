#!/usr/bin/env python3
import argparse
import base64
import hashlib
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

try:
    import httpx
except ImportError:
    sys.exit('httpx required:  pip install "httpx[http2]"')

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

BASE = "https://www2.hthomeservice.com"
HERE = Path(__file__).resolve().parent
ENV_FILE = HERE / ".env"
COOKIE_FILE = HERE / ".ht_cookies.json"

CTOC = "/proxy/ctoc"
BEARER = "/proxy/bearer/api/v1"

WIND_VALUES = ("light", "mid", "pow")
WIND_ALIASES = {
    "light": "light", "low": "light", "1": "light",
    "mid": "mid", "medium": "mid", "2": "mid",
    "pow": "pow", "high": "pow", "strong": "pow", "3": "pow",
}

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
    "Origin": BASE,
    "Referer": BASE + "/",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
}

CTOC_TOKEN_EXPIRED = {"704", "705"}
AUTH_EXPIRED = {"701", "706", "109"}

ENV_KEYS = ("HT_ID", "HT_PW", "HT_SITE_ID", "HT_DONG", "HT_HO", "HT_FAN_ID",
            "HT_HTTP2", "HT_BROWSER_HEADERS", "HT_CHARSET", "HT_TIMEOUT",
            "TG_BOT_TOKEN", "TG_CHAT_ID")


def load_env(path=ENV_FILE):
    cfg = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            cfg[k.strip()] = v.strip().strip('"').strip("'")
    for k in ENV_KEYS:
        if os.environ.get(k):
            cfg[k] = os.environ[k]
    return cfg


def truthy(v):
    return str(v or "").strip().lower() in ("1", "true", "yes", "on")


def _evp_bytes_to_key(password, salt, key_len=32, iv_len=16):
    d, prev = b"", b""
    while len(d) < key_len + iv_len:
        prev = hashlib.md5(prev + password + salt).digest()
        d += prev
    return d[:key_len], d[key_len:key_len + iv_len]


def _aes_cbc_encrypt(key, iv, data):
    try:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        enc = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
        return enc.update(data) + enc.finalize()
    except ImportError:
        try:
            from Crypto.Cipher import AES
        except ImportError:
            sys.exit("need cryptography (or pycryptodome):  pip install cryptography")
        return AES.new(key, AES.MODE_CBC, iv).encrypt(data)


def cryptojs_encrypt(plain, passphrase="hTsEcret", salt=None):
    """crypto-js AES.encrypt(text, passphrase) 와 동일한 OpenSSL Salted__ 포맷."""
    salt = salt if salt is not None else os.urandom(8)
    key, iv = _evp_bytes_to_key(passphrase.encode("utf-8"), salt)
    data = plain.encode("utf-8")
    pad = 16 - len(data) % 16
    data += bytes([pad]) * pad
    return base64.b64encode(b"Salted__" + salt + _aes_cbc_encrypt(key, iv, data)).decode()


class HTError(RuntimeError):
    pass


class HTTimeout(HTError):
    pass


class HTClient:
    def __init__(self, cfg, *, http2=None, browser_headers=None, charset=None, timeout=None, verbose=False):
        self.cfg = cfg
        self.verbose = verbose
        self.http2 = truthy(cfg.get("HT_HTTP2")) if http2 is None else http2
        self.browser_headers = truthy(cfg.get("HT_BROWSER_HEADERS")) if browser_headers is None else browser_headers
        self.charset = truthy(cfg.get("HT_CHARSET")) if charset is None else charset
        self.timeout = float(cfg.get("HT_TIMEOUT") or 20) if timeout is None else timeout
        self.client = self._new_client()
        self._load_cookies()

    def _new_client(self):
        headers = dict(BROWSER_HEADERS) if self.browser_headers else {}
        try:
            return httpx.Client(base_url=BASE, http2=self.http2, timeout=self.timeout,
                                headers=headers, follow_redirects=False)
        except ImportError:
            raise HTError("HTTP/2 needs h2:  pip install h2")

    def _json_headers(self):
        return {"Content-Type": "application/json;charset=UTF-8" if self.charset else "application/json"}

    def _log(self, *a):
        if self.verbose:
            print("[ht]", *a, file=sys.stderr)

    def raw(self, method, path, body=None, **kw):
        t = time.perf_counter()
        headers = dict(kw.pop("headers", {}) or {})
        if body is not None:
            headers.update(self._json_headers())
            kw["content"] = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        try:
            r = self.client.request(method, path, headers=headers, **kw)
        except httpx.TimeoutException as e:
            self._log(f"{method} {path} -> timeout {time.perf_counter()-t:.1f}s")
            raise HTTimeout(f"{method} {path} no response (timeout {self.timeout}s)") from e
        self._log(f"{method} {path} -> {r.status_code} {r.http_version} {1000*(time.perf_counter()-t):.0f}ms")
        return r

    def _load_cookies(self):
        if not COOKIE_FILE.exists():
            return
        try:
            for c in json.loads(COOKIE_FILE.read_text(encoding="utf-8")):
                self.client.cookies.set(c["name"], c["value"], domain=c.get("domain") or "", path=c.get("path") or "/")
        except Exception as e:
            self._log("cookie load failed:", e)

    def save_cookies(self):
        items = [{"name": c.name, "value": c.value, "domain": c.domain, "path": c.path}
                 for c in self.client.cookies.jar]
        COOKIE_FILE.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
        try:
            os.chmod(COOKIE_FILE, 0o600)
        except OSError:
            pass

    def login(self):
        uid, pw = self.cfg.get("HT_ID"), self.cfg.get("HT_PW")
        if not uid or not pw:
            raise HTError("HT_ID / HT_PW not set in .env")
        r = self.raw("POST", "/login",
                     {"id": cryptojs_encrypt(uid), "password": cryptojs_encrypt(pw), "rememberMe": True})
        if r.status_code != 200:
            hint = "  (409: session conflict or bad encryption)" if r.status_code == 409 else ""
            raise HTError(f"login failed HTTP {r.status_code}: {r.text[:200]}{hint}")
        if not self.is_authenticated():
            raise HTError(f"login 200 but session invalid: {r.text[:200]}")
        self.save_cookies()

    def is_authenticated(self):
        try:
            r = self.raw("POST", "/isAuthenticated", {})
        except HTTimeout:
            return None
        if r.status_code != 200:
            return None
        try:
            d = r.json()
        except ValueError:
            return None
        return d if isinstance(d, dict) and d.get("id") else None

    def refresh_login(self):
        try:
            r = self.raw("POST", "/loginWithRefreshToken", {})
        except HTTimeout:
            return False
        ok = r.status_code == 200 and bool(self.is_authenticated())
        if ok:
            self.save_cookies()
        return ok

    def household(self):
        r = self.raw("GET", BEARER + "/user/danji/household")
        if r.status_code >= 400:
            raise HTError(f"household failed HTTP {r.status_code}: {r.text[:200]}")
        d = r.json()
        for key in ("resultData", "data"):
            if isinstance(d.get(key), dict) and "danjiList" in d[key]:
                return d[key]["danjiList"] or []
        return d.get("danjiList") or []

    def pick_household(self):
        cfg = self.cfg
        if cfg.get("HT_SITE_ID") and cfg.get("HT_DONG") and cfg.get("HT_HO"):
            return str(cfg["HT_SITE_ID"]), str(cfg["HT_DONG"]), str(cfg["HT_HO"])
        for h in self.household():
            if h.get("isApproved", True):
                return str(h["siteId"]), str(h["dong"]), str(h["ho"])
        raise HTError("no approved household")

    def ctoc_token(self):
        site, dong, ho = self.pick_household()
        r = self.raw("POST", "/getctoctoken",
                     {"siteId": site, "dong": dong, "ho": ho, "clientId": "HT-WEB", "uuid": ""})
        if r.status_code == 403:
            self._log("getctoctoken 403, token already issued")
        elif r.status_code != 200:
            raise HTError(f"getctoctoken failed HTTP {r.status_code}: {r.text[:200]}")
        self.save_cookies()
        return r

    def ctoc_refresh(self):
        try:
            r = self.raw("POST", "/loginWithCtoCRefreshToken", {})
        except HTTimeout:
            return False
        if r.status_code == 200:
            self.save_cookies()
            return True
        return False

    def ensure_session(self):
        if not self.is_authenticated():
            if not self.refresh_login():
                self.login()
        self.ctoc_token()

    def ctoc(self, method, path, body=None, _retry=2):
        r = self.raw(method, CTOC + path, body)
        code = ""
        data = {}
        if "json" in r.headers.get("content-type", ""):
            try:
                data = r.json()
            except ValueError:
                data = {}
            if isinstance(data, dict):
                code = str(data.get("resultCode", ""))
        if _retry > 0:
            if code in CTOC_TOKEN_EXPIRED or (r.status_code == 500 and "Invalid Token" in r.text):
                if not self.ctoc_refresh():
                    self.ctoc_token()
                return self.ctoc(method, path, body, _retry - 1)
            if r.status_code == 401 or code in AUTH_EXPIRED:
                if not self.refresh_login():
                    self.login()
                self.ctoc_token()
                return self.ctoc(method, path, body, _retry - 1)
        if r.status_code >= 400:
            raise HTError(f"{method} {path} HTTP {r.status_code}: {r.text[:300]}")
        return data if isinstance(data, dict) else {}

    def devices(self, device_type="fan"):
        d = self.ctoc("GET", f"/devices?deviceType={device_type}")
        return (d.get("data") or {}).get("deviceList") or []

    def fan_id(self):
        if self.cfg.get("HT_FAN_ID"):
            return str(self.cfg["HT_FAN_ID"])
        fans = self.devices("fan")
        if not fans:
            raise HTError("no fan device")
        return str(fans[0]["id"])

    def fan_status(self, fid):
        return self.ctoc("GET", f"/fans/{fid}").get("data") or {}

    def fan_control(self, fid, commands):
        return self.ctoc("PUT", f"/fans/{fid}", {"commandList": commands}).get("data") or {}

    def fan_control_or_poll(self, fid, commands, polls=3, interval=5):
        try:
            return self.fan_control(fid, commands), False
        except HTTimeout:
            print(f"[warn] no response ({self.timeout}s), polling status", file=sys.stderr)
            st = {}
            for _ in range(polls):
                time.sleep(interval)
                st = self.fan_status(fid)
                m = status_map(st)
                if all(m.get(c["command"]) == c["value"] for c in commands):
                    return st, True
            return st, True


def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def notify(cfg, text):
    token, chat = cfg.get("TG_BOT_TOKEN"), cfg.get("TG_CHAT_ID")
    if not token or not chat:
        return
    try:
        httpx.post(f"https://api.telegram.org/bot{token}/sendMessage",
                   json={"chat_id": chat, "text": text}, timeout=10)
    except Exception:
        pass


def status_map(data):
    return {s.get("command"): s.get("value") for s in (data or {}).get("statusList") or []}


def fmt_status(data):
    m = status_map(data)
    return f"power={m.get('power', '?')} wind={m.get('wind', '?')} state={data.get('state', '?')}"


def norm_wind(v):
    w = WIND_ALIASES.get(str(v).strip().lower())
    if not w:
        raise HTError(f"wind must be one of {'/'.join(WIND_VALUES)} (got {v})")
    return w


def cmd_status(c, a):
    c.ensure_session()
    fid = c.fan_id()
    d = c.fan_status(fid)
    print(json.dumps(d, ensure_ascii=False, indent=2) if a.json else f"{now_str()}  {fid}: {fmt_status(d)}")


def cmd_devices(c, a):
    c.ensure_session()
    lst = c.devices("fan")
    if a.json:
        print(json.dumps(lst, ensure_ascii=False, indent=2))
    else:
        for d in lst:
            print(f"{d.get('id')}  {d.get('deviceType')}  {d.get('deviceName')}  "
                  f"{d.get('deviceLocation')}  {d.get('state')}")


def cmd_power(c, a, value):
    c.ensure_session()
    fid = c.fan_id()
    # 전원과 풍량을 한 요청에 같이 보내면 풍량이 무시되므로 전원을 먼저 켠 뒤 풍량을 따로 보낸다.
    d, timed_out = c.fan_control_or_poll(fid, [{"command": "power", "value": value}])
    ok_power = status_map(d).get("power") == value
    want_wind = norm_wind(a.wind) if (value == "on" and getattr(a, "wind", None)) else None
    if want_wind and ok_power:
        time.sleep(2)
        d2, to2 = c.fan_control_or_poll(fid, [{"command": "wind", "value": want_wind}])
        if status_map(d2):
            d, timed_out = d2, to2
    m = status_map(d)
    tag = " (timeout, re-read)" if timed_out else ""
    print(f"{now_str()}  {fid}: {fmt_status(d)}{tag}")
    if not ok_power:
        notify(c.cfg, f"⚠️ 환기 {'켜기' if value == 'on' else '끄기'} 실패  {now_str()}")
        return 2
    if want_wind and m.get("wind") != want_wind:
        print(f"[warn] wind {want_wind} not applied (now {m.get('wind')})", file=sys.stderr)
    if value == "on":
        notify(c.cfg, f"🌀 환기 켜짐 · 풍량 {m.get('wind')}  {now_str()}")
    else:
        notify(c.cfg, f"💤 환기 꺼짐  {now_str()}")
    return 0


def cmd_wind(c, a):
    c.ensure_session()
    fid = c.fan_id()
    d, timed_out = c.fan_control_or_poll(fid, [{"command": "wind", "value": norm_wind(a.value)}])
    print(f"{now_str()}  {fid}: {fmt_status(d)}{' (timeout, re-read)' if timed_out else ''}")


DIAG_VARIANTS = [
    ("HTTP/1.1  application/json", dict(http2=False, browser_headers=False, charset=False)),
    ("HTTP/1.1  application/json;charset=UTF-8", dict(http2=False, browser_headers=False, charset=True)),
    ("HTTP/1.1  browser headers + charset", dict(http2=False, browser_headers=True, charset=True)),
    ("HTTP/2    application/json", dict(http2=True, browser_headers=False, charset=False)),
    ("HTTP/2    browser headers + charset", dict(http2=True, browser_headers=True, charset=True)),
]


def cmd_diag(c, a):
    c.ensure_session()
    fid = c.fan_id()
    cur = status_map(c.fan_status(fid))
    power = cur.get("power") or "off"
    target = ({"on": "off", "off": "on"}[power] if a.flip else power)
    body = {"commandList": [{"command": "power", "value": target}]}
    print(f"{fid}, power={power} -> PUT power={target} "
          f"({'flip' if a.flip else 'same value'}), timeout {a.timeout}s\n")

    results = []
    for label, opt in DIAG_VARIANTS:
        if opt["http2"]:
            try:
                import h2  # noqa: F401
            except ImportError:
                print(f"- {label}\n    -> skipped (pip install h2)")
                continue
        sub = HTClient(c.cfg, timeout=a.timeout, verbose=False, **opt)
        t = time.perf_counter()
        ok = False
        try:
            r = sub.raw("PUT", CTOC + f"/fans/{fid}", body)
            ms = 1000 * (time.perf_counter() - t)
            ok = r.status_code == 200
            print(f"- {label}\n    -> HTTP {r.status_code} via {r.http_version} in {ms:.0f}ms  "
                  f"{r.text[:160].replace(chr(10), ' ')}")
        except HTTimeout:
            print(f"- {label}\n    -> TIMEOUT ({a.timeout}s)")
            if a.flip:
                time.sleep(3)
                now = status_map(c.fan_status(fid)).get("power")
                print(f"       after timeout: power={now}  "
                      f"({'applied, response path issue' if now == target else 'not applied'})")
                if now == target:
                    ok = True
        results.append((label, ok, opt))
        if a.flip and ok:
            try:
                sub.raw("PUT", CTOC + f"/fans/{fid}", {"commandList": [{"command": "power", "value": power}]})
            except HTTimeout:
                pass
            time.sleep(3)
            print(f"       restored: power={status_map(c.fan_status(fid)).get('power')}")
        if ok and not a.all:
            break

    good = [x for x in results if x[1]]
    print()
    if good:
        _, _, opt = good[0]
        print("working combination for .env:")
        print(f"  HT_HTTP2={int(opt['http2'])}\n  HT_BROWSER_HEADERS={int(opt['browser_headers'])}\n"
              f"  HT_CHARSET={int(opt['charset'])}")
    else:
        print("all variants failed. try `diag --flip`, logout other sessions, or --timeout 90.")


def main(argv=None):
    p = argparse.ArgumentParser(description="현대HT 홈서비스 환기 제어")
    p.add_argument("-v", "--verbose", action="store_true")
    p.add_argument("--timeout", type=float, default=None)
    p.add_argument("--http2", dest="http2", action="store_true", default=None)
    p.add_argument("--http1", dest="http2", action="store_false")
    p.add_argument("--browser-headers", action="store_true", default=None)
    p.add_argument("--charset", action="store_true", default=None)
    p.add_argument("--json", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    sub.add_parser("devices")
    s_on = sub.add_parser("on")
    s_on.add_argument("--wind", help="light | mid | pow")
    sub.add_parser("off")
    s_w = sub.add_parser("wind")
    s_w.add_argument("value")
    s_d = sub.add_parser("diag")
    s_d.add_argument("--all", action="store_true")
    s_d.add_argument("--flip", action="store_true")
    a = p.parse_args(argv)

    cfg = load_env()
    if a.timeout is None:
        a.timeout = 15.0 if a.cmd == "diag" else float(cfg.get("HT_TIMEOUT") or 20)
    try:
        c = HTClient(cfg, http2=a.http2, browser_headers=a.browser_headers, charset=a.charset,
                     timeout=a.timeout, verbose=a.verbose)
        handlers = {
            "status": cmd_status, "devices": cmd_devices, "wind": cmd_wind, "diag": cmd_diag,
            "on": lambda c, a: cmd_power(c, a, "on"), "off": lambda c, a: cmd_power(c, a, "off"),
        }
        return int(handlers[a.cmd](c, a) or 0)
    except HTTimeout as e:
        print("error:", e, file=sys.stderr)
        return 2
    except HTError as e:
        print("error:", e, file=sys.stderr)
        return 1
    except httpx.HTTPError as e:
        print("http error:", e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
