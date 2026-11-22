"""Fetch H4 data, compute indicators/signals, match news -> data/dashboard.json.
Stdlib only (no pip install needed)."""
import json, os, re, time, urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

DATA = os.path.join(os.path.dirname(__file__), "..", "data")
UA = {"User-Agent": "Mozilla/5.0 (dashboard-bot)"}
H4 = 4 * 3600 * 1000
MOVE = 3.0  # % threshold

ASSETS = [
    {"id": "BTCUSDT", "name": "Bitcoin", "src": "binance", "tv": "BINANCE:BTCUSDT", "kw": r"bitcoin|\bbtc\b"},
    {"id": "ETHUSDT", "name": "Ethereum", "src": "binance", "tv": "BINANCE:ETHUSDT", "kw": r"ethereum|\beth\b|ether\b"},
    {"id": "BNBUSDT", "name": "BNB", "src": "binance", "tv": "BINANCE:BNBUSDT", "kw": r"\bbnb\b|binance"},
    {"id": "BTC.D", "name": "BTC Dominance", "src": "btcd", "tv": "CRYPTOCAP:BTC.D", "kw": r"dominance|altcoin"},
    {"id": "OILBRENT", "name": "Dầu Brent", "src": "yahoo", "tv": "TVC:UKOIL", "kw": r"\boil\b|brent|crude|opec"},
]
FEEDS = ["https://www.coindesk.com/arc/outboundfeeds/rss/", "https://cointelegraph.com/rss",
         "https://oilprice.com/rss/main"]


def get(url, raw=False):
    for i in range(3):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30) as r:
                b = r.read()
                return b if raw else json.loads(b)
        except Exception as e:
            err = e
            time.sleep(3)
    raise err


def load(name, default):
    try:
        with open(os.path.join(DATA, name)) as f:
            return json.load(f)
    except Exception:
        return default


def save(name, obj):
    with open(os.path.join(DATA, name), "w") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))


# ---------- data sources: return list of [t_ms, o, h, l, c] (closed candles only) ----------
def binance(sym):
    k = get(f"https://data-api.binance.vision/api/v3/klines?symbol={sym}&interval=4h&limit=500")
    now = time.time() * 1000
    return [[x[0], float(x[1]), float(x[2]), float(x[3]), float(x[4])] for x in k if x[6] < now]


def yahoo():
    j = get("https://query1.finance.yahoo.com/v8/finance/chart/BZ=F?interval=1h&range=60d")["chart"]["result"][0]
    q = j["indicators"]["quote"][0]
    buckets = {}
    for i, t in enumerate(j["timestamp"]):
        if q["close"][i] is None:
            continue
        b = (t * 1000) // H4 * H4
        o, h, l, c = q["open"][i], q["high"][i], q["low"][i], q["close"][i]
        if b in buckets:
            x = buckets[b]; x[2] = max(x[2], h); x[3] = min(x[3], l); x[4] = c
        else:
            buckets[b] = [b, o, h, l, c]
    now = time.time() * 1000
    return [buckets[k] for k in sorted(buckets) if k + H4 < now]


def btcd():
    hist = load("btcd_history.json", [])
    v = get("https://api.coingecko.com/api/v3/global")["data"]["market_cap_percentage"]["btc"]
    b = int(time.time() * 1000) // H4 * H4
    if not hist or hist[-1][0] != b:
        hist.append([b, v, v, v, v])
    else:
        x = hist[-1]; x[2] = max(x[2], v); x[3] = min(x[3], v); x[4] = v
    hist = hist[-500:]
    save("btcd_history.json", hist)
    return hist


# ---------- indicators ----------
def ema(v, n):
    k, out, e = 2 / (n + 1), [], None
    for x in v:
        e = x if e is None else x * k + e * (1 - k)
        out.append(e)
    return out


def rsi(c, n=14):
    out, ag, al = [50.0] * len(c), 0.0, 0.0
    for i in range(1, len(c)):
        d = c[i] - c[i - 1]
        g, l = max(d, 0), max(-d, 0)
        if i <= n:
            ag += g / n; al += l / n
        else:
            ag = (ag * (n - 1) + g) / n; al = (al * (n - 1) + l) / n
        if i >= n:
            out[i] = 100 if al == 0 else 100 - 100 / (1 + ag / al)
    return out


def atr(k, n=14):
    tr = [k[0][2] - k[0][3]] + [max(k[i][2] - k[i][3], abs(k[i][2] - k[i - 1][4]), abs(k[i][3] - k[i - 1][4]))
                                for i in range(1, len(k))]
    out, a = [], tr[0]
    for x in tr:
        a = (a * (n - 1) + x) / n
        out.append(a)
    return out


def scores(c):
    e20, e50, e200, r = ema(c, 20), ema(c, 50), ema(c, 200), rsi(c)
    macd = [a - b for a, b in zip(ema(c, 12), ema(c, 26))]
    hist = [a - b for a, b in zip(macd, ema(macd, 9))]
    s = []
    for i in range(len(c)):
        x = (1 if c[i] > e50[i] else -1) + (1 if e20[i] > e50[i] else -1) + (1 if hist[i] > 0 else -1)
        x += 1 if c[i] > e200[i] else -1
        x += 1 if r[i] > 55 else (-1 if r[i] < 45 else 0)
        s.append(x)
    return s, dict(ema20=e20[-1], ema50=e50[-1], ema200=e200[-1], rsi=r[-1], macd_hist=hist[-1])


def label(x):
    return "LONG" if x >= 3 else "SHORT" if x <= -3 else "NEUTRAL"


def analyze(a, k):
    c = [x[4] for x in k]
    s, ind = scores(c)
    sig = label(s[-1])
    # Historical stats: next 6 candles (24h) after the same signal
    rets = [(c[i + 6] / c[i] - 1) * 100 for i in range(50, len(c) - 6) if label(s[i]) == sig]
    fc = None
    if len(rets) >= 5:
        fc = dict(avg=sum(rets) / len(rets), up=sum(r > 0 for r in rets) / len(rets) * 100, n=len(rets))
    order = None
    if a["src"] != "btcd" and sig != "NEUTRAL":
        at, p, d = atr(k)[-1], c[-1], 1 if sig == "LONG" else -1
        order = dict(side=sig, entry=p, sl=p - d * 1.5 * at, tp1=p + d * 1.5 * at, tp2=p + d * 3 * at)
    moves = [dict(t=x[0], chg=(x[4] / x[1] - 1) * 100) for x in k[-60:] if abs(x[4] / x[1] - 1) * 100 >= MOVE]
    return dict(id=a["id"], name=a["name"], tv=a["tv"], price=c[-1], t=k[-1][0],
                chg4h=(c[-1] / c[-2] - 1) * 100 if len(c) > 1 else 0,
                chg24h=(c[-1] / c[-7] - 1) * 100 if len(c) > 6 else 0,
                score=s[-1], signal=sig, ind=ind, forecast=fc, order=order, moves=moves,
                spark=c[-60:], candles=len(k))


def news():
    store = {n["link"]: n for n in load("news.json", [])}
    for u in FEEDS:
        try:
            root = ET.fromstring(get(u, raw=True))
            for it in root.iter("item"):
                link, title = it.findtext("link", "").strip(), it.findtext("title", "").strip()
                try:
                    t = int(parsedate_to_datetime(it.findtext("pubDate")).timestamp() * 1000)
                except Exception:
                    continue
                if link and link not in store:
                    store[link] = dict(t=t, title=title, link=link, src=re.sub(r"https?://(www\.)?([^/]+).*", r"\2", u))
        except Exception as e:
            print("feed fail", u, e)
    cutoff = time.time() * 1000 - 30 * 86400000
    items = sorted((n for n in store.values() if n["t"] > cutoff), key=lambda n: -n["t"])[:800]
    save("news.json", items)
    return items


def main():
    os.makedirs(DATA, exist_ok=True)
    nw, out = news(), []
    for a in ASSETS:
        try:
            k = binance(a["id"]) if a["src"] == "binance" else yahoo() if a["src"] == "yahoo" else btcd()
            r = analyze(a, k)
            kw = re.compile(a["kw"], re.I)
            for m in r["moves"]:  # news within [-8h, +4h] of the move candle
                m["news"] = [n for n in nw if m["t"] - 2 * H4 <= n["t"] <= m["t"] + 2 * H4 and kw.search(n["title"])][:5]
            r["latest_news"] = [n for n in nw if kw.search(n["title"])][:5]
            out.append(r)
        except Exception as e:
            print("asset fail", a["id"], e)
            out.append(dict(id=a["id"], name=a["name"], tv=a["tv"], error=str(e)))
    save("dashboard.json", dict(updated=int(time.time() * 1000), threshold=MOVE, assets=out))
    print("ok", [(x["id"], x.get("signal"), x.get("error")) for x in out])


if __name__ == "__main__":
    main()
