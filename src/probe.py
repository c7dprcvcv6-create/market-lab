import datetime
import json
import pathlib
import time
import urllib.error
import urllib.request

UA = {"User-Agent": "Mozilla/5.0 (market-lab probe)"}
TICKERS = ["BTC-USD", "ETH-USD", "BMNR", "COIN", "MSTR", "SPY", "QQQ",
           "NVDA", "TSLA", "AAPL", "MSFT", "JPM", "KO", "XOM", "GLD"]
lines = []


def log(s):
    print(s)
    lines.append(s)


def get(url, timeout=30):
    req = urllib.request.Request(url, headers=UA)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, ""
    except Exception as e:
        return -1, str(e)[:100]


def day(ts):
    d = datetime.datetime.fromtimestamp(ts, datetime.timezone.utc)
    return d.strftime("%Y-%m-%d")


log("probe time: " + datetime.datetime.now(datetime.timezone.utc).isoformat())
log("")
log("== Yahoo chart (no key) ==")
for t in TICKERS:
    url = ("https://query1.finance.yahoo.com/v8/finance/chart/"
           + t + "?range=max&interval=1d")
    status, body = get(url)
    if status == 200:
        try:
            res = json.loads(body)["chart"]["result"][0]
            ts = res["timestamp"]
            log(f"{t}: ok rows={len(ts)} first={day(ts[0])} last={day(ts[-1])}")
        except Exception as e:
            log(f"{t}: status 200 but parse failed: {str(e)[:80]}")
    else:
        log(f"{t}: status={status} {body[:80]}")
    time.sleep(2)

log("")
log("== Other sources (small test) ==")
tests = {
    "stooq AAPL csv": "https://stooq.com/q/d/l/?s=aapl.us&i=d",
    "bitstamp BTC ohlc": "https://www.bitstamp.net/api/v2/ohlc/btcusd/?step=86400&limit=5",
    "coinbase BTC candles": "https://api.exchange.coinbase.com/products/BTC-USD/candles?granularity=86400",
    "binance BTC klines": "https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1d&limit=5",
    "binance-vision BTC klines": "https://data-api.binance.vision/api/v3/klines?symbol=BTCUSDT&interval=1d&limit=5",
}
for name, url in tests.items():
    status, body = get(url)
    log(f"{name}: status={status} bytes={len(body)} head={body[:60]!r}")
    time.sleep(1)

p = pathlib.Path("data")
p.mkdir(exist_ok=True)
(p / "probe_report.txt").write_text("\n".join(lines) + "\n")
