import csv
import datetime as dt
import json
import pathlib
import statistics
import time
import urllib.error
import urllib.request

UA = {"User-Agent": "Mozilla/5.0 (market-lab)"}
TICKERS = ["BTC-USD", "ETH-USD", "BMNR", "COIN", "MSTR", "SPY", "QQQ",
           "NVDA", "TSLA", "AAPL", "MSFT", "JPM", "KO", "XOM", "GLD"]
CRYPTO = {"BTC-USD", "ETH-USD"}
START_YEAR = {"BTC-USD": 2014, "ETH-USD": 2017, "BMNR": 2025,
              "COIN": 2021, "TSLA": 2010, "GLD": 2004}
DEFAULT_START = 1990
NOW = dt.datetime.now(dt.timezone.utc)
TODAY = NOW.date()
OUT = pathlib.Path("data/prices")
OUT.mkdir(parents=True, exist_ok=True)
report = []


def log(s):
    print(s)
    report.append(s)


def http_get(url, tries=3):
    last = (-1, "")
    for i in range(tries):
        req = urllib.request.Request(url, headers=UA)
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status, r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            last = (e.code, "")
            if e.code == 404:
                return last
        except Exception as e:
            last = (-1, str(e)[:100])
        time.sleep(3 * (i + 1))
    return last


def epoch(d):
    x = dt.datetime(d.year, d.month, d.day, tzinfo=dt.timezone.utc)
    return int(x.timestamp())


def fetch_window(t, d1, d2):
    url = ("https://query1.finance.yahoo.com/v8/finance/chart/" + t
           + "?period1=" + str(epoch(d1)) + "&period2=" + str(epoch(d2))
           + "&interval=1d")
    status, body = http_get(url)
    if status == 404:
        return {}
    if status != 200:
        raise RuntimeError(f"{t} {d1}->{d2} status={status} {body[:60]}")
    res = json.loads(body)["chart"]["result"]
    if not res or "timestamp" not in res[0]:
        return {}
    r = res[0]
    ts = r["timestamp"]
    q = r["indicators"]["quote"][0]
    adj = None
    if r["indicators"].get("adjclose"):
        adj = r["indicators"]["adjclose"][0]["adjclose"]
    rows = {}
    for i, s in enumerate(ts):
        c = q["close"][i]
        if c is None:
            continue
        d = dt.datetime.fromtimestamp(s, dt.timezone.utc).date()
        a = adj[i] if adj and adj[i] is not None else c
        rows[d] = [q["open"][i], q["high"][i], q["low"][i], c, a,
                   q["volume"][i]]
    return rows


def get_history(t):
    rows = {}
    y = START_YEAR.get(t, DEFAULT_START)
    while y <= TODAY.year:
        d1 = dt.date(y, 1, 1)
        d2 = dt.date(min(y + 5, TODAY.year + 1), 1, 1)
        rows.update(fetch_window(t, d1, d2))
        time.sleep(1.5)
        y += 5
    return {d: v for d, v in rows.items() if d < TODAY}


def check(t, rows):
    ds = sorted(rows)
    if len(ds) < 30:
        return False, f"{t}: FAILED rows={len(ds)}"
    gaps = [(ds[i] - ds[i - 1]).days for i in range(1, len(ds))]
    med = statistics.median(gaps)
    if med > 4:
        return False, f"{t}: NOT_DAILY rows={len(ds)} median_gap={med}d"
    limit = 1 if t in CRYPTO else 5
    over = len([g for g in gaps if g > limit])
    moves = []
    for i in range(1, len(ds)):
        a = rows[ds[i - 1]][3]
        b = rows[ds[i]][3]
        if a:
            moves.append((abs(b / a - 1) * 100, ds[i]))
    mm = max(moves)
    msg = (f"{t}: ok rows={len(ds)} first={ds[0]} last={ds[-1]} "
           f"median_gap={med}d gaps_over_{limit}d={over} "
           f"max_gap={max(gaps)}d max_1d_move={mm[0]:.1f}%@{mm[1]}")
    return True, msg


def write_csv(t, rows):
    with open(OUT / (t + ".csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "open", "high", "low", "close",
                    "adjclose", "volume"])
        for d in sorted(rows):
            w.writerow([d.isoformat()] + rows[d])


def binance_daily(symbol):
    out = {}
    start = int(dt.datetime(2017, 8, 17,
                            tzinfo=dt.timezone.utc).timestamp() * 1000)
    now_ms = int(NOW.timestamp() * 1000)
    while start < now_ms:
        url = ("https://data-api.binance.vision/api/v3/klines?symbol="
               + symbol + "&interval=1d&limit=1000&startTime=" + str(start))
        status, body = http_get(url)
        if status != 200:
            raise RuntimeError(f"binance {symbol} status={status}")
        k = json.loads(body)
        if not k:
            break
        for c in k:
            d = dt.datetime.fromtimestamp(c[0] / 1000, dt.timezone.utc).date()
            out[d] = float(c[4])
        start = k[-1][0] + 86400000
        time.sleep(1)
    return out


def crosscheck(t, rows, symbol):
    try:
        b = binance_daily(symbol)
        diffs = []
        for d, v in rows.items():
            if d in b and b[d]:
                diffs.append((abs(v[3] / b[d] - 1) * 100, d))
        if not diffs:
            log(f"XCHK {t}: no overlapping days")
            return
        med = statistics.median([x[0] for x in diffs])
        mx = max(diffs)
        log(f"XCHK {t} vs Binance {symbol}: days={len(diffs)} "
            f"median_diff={med:.2f}% max_diff={mx[0]:.2f}%@{mx[1]}")
    except Exception as e:
        log(f"XCHK {t}: error {str(e)[:80]}")


log("backfill time: " + NOW.isoformat())
data = {}
for t in TICKERS:
    try:
        rows = get_history(t)
        ok, msg = check(t, rows)
        log(msg)
        if ok:
            write_csv(t, rows)
            data[t] = rows
    except Exception as e:
        log(f"{t}: ERROR {str(e)[:120]}")
log("")
if "BTC-USD" in data:
    crosscheck("BTC-USD", data["BTC-USD"], "BTCUSDT")
if "ETH-USD" in data:
    crosscheck("ETH-USD", data["ETH-USD"], "ETHUSDT")
pathlib.Path("data/quality_report.txt").write_text("\n".join(report) + "\n")
