import bisect
import csv
import datetime as dt
import math
import os
import pathlib
import sys

MODE = (os.environ.get("MODE") or "dev").strip()
HOLDOUT_START = dt.date(2023, 10, 24)
HORIZONS = [1, 3, 5, 21]
MODELS = ["exp", "v20", "v60", "ewma"]
PRIMARY = "ewma"
POOL_START = 60
MINPOOL = 250
START = POOL_START + MINPOOL + max(HORIZONS)
MIN_ASSET_N = 100
A68 = 0.317
A95 = 0.05
TICKERS = ["BTC-USD", "ETH-USD", "BMNR", "COIN", "MSTR", "SPY", "QQQ",
           "NVDA", "TSLA", "AAPL", "MSFT", "JPM", "KO", "XOM", "GLD"]
POOL_EXCLUDE = {"BMNR"}
PRICES = pathlib.Path("data/prices")
OUT = pathlib.Path("data/results")
OUT.mkdir(parents=True, exist_ok=True)

if MODE not in ("dev", "holdout"):
    print("MODE must be dev or holdout")
    sys.exit(1)
SUMFILE = OUT / ("range_" + MODE + ".txt")
if MODE == "holdout" and SUMFILE.exists():
    print("holdout already used. delete the result file to run again")
    sys.exit(1)

lines = []


def log(s=""):
    print(s)
    lines.append(s)


def load(t):
    ds, cs = [], []
    with open(PRICES / (t + ".csv")) as f:
        for row in csv.DictReader(f):
            c = None
            for key in ("adjclose", "close"):
                try:
                    c = float(row[key])
                    break
                except (ValueError, TypeError, KeyError):
                    c = None
            if c is None or c <= 0:
                continue
            ds.append(dt.date.fromisoformat(row["date"]))
            cs.append(c)
    return ds, cs


def roll_std(lr, p):
    out = [None] * len(lr)
    for i in range(p, len(lr)):
        w = lr[i - p + 1:i + 1]
        m = sum(w) / p
        out[i] = math.sqrt(max(sum((x - m) ** 2 for x in w) / (p - 1), 1e-12))
    return out


def exp_std(lr):
    out = [None] * len(lr)
    s = ss = 0.0
    cnt = 0
    for i in range(1, len(lr)):
        x = lr[i]
        s += x
        ss += x * x
        cnt += 1
        if cnt >= 30:
            m = s / cnt
            out[i] = math.sqrt(max(ss / cnt - m * m, 1e-12))
    return out


def ewma_std(lr, lam=0.94):
    out = [None] * len(lr)
    var = None
    for i in range(20, len(lr)):
        if i == 20:
            var = sum(y * y for y in lr[1:21]) / 20.0
        else:
            var = lam * var + (1 - lam) * lr[i] * lr[i]
        out[i] = math.sqrt(max(var, 1e-12))
    return out


def sigmas(lr):
    return {"exp": exp_std(lr), "v20": roll_std(lr, 20),
            "v60": roll_std(lr, 60), "ewma": ewma_std(lr)}


def quant(pool, p):
    return pool[int(p * (len(pool) - 1))]


acc = {}
span = {}
data = {}
for tk in TICKERS:
    try:
        ds, cs = load(tk)
    except FileNotFoundError:
        log("missing file: " + tk)
        continue
    n = len(cs)
    if n < START + 30:
        log("too short: " + tk)
        continue
    lr = [0.0] + [math.log(cs[i] / cs[i - 1]) for i in range(1, n)]
    sig = sigmas(lr)
    data[tk] = (ds, cs, sig)
    for h in HORIZONS:
        sq = math.sqrt(h)
        pools = {m: [] for m in MODELS}
        for t in range(POOL_START + h, n - h):
            t0 = t - h
            r0 = abs(math.log(cs[t] / cs[t0]))
            for m in MODELS:
                bisect.insort(pools[m], r0 / (sig[m][t0] * sq))
            if t < START:
                continue
            if MODE == "dev":
                if ds[t + h] >= HOLDOUT_START:
                    break
            elif ds[t] < HOLDOUT_START:
                continue
            ar = abs(math.log(cs[t + h] / cs[t]))
            lo, hi = span.get(tk, (ds[t], ds[t]))
            span[tk] = (min(lo, ds[t]), max(hi, ds[t + h]))
            for m in MODELS:
                pool = pools[m]
                if len(pool) < MINPOOL:
                    continue
                sg = sig[m][t] * sq
                b68 = quant(pool, 0.683) * sg
                b95 = quant(pool, 0.95) * sg
                a = acc.setdefault((m, h, tk), [0] * 5 + [0.0] * 4)
                a[0] += 1
                a[1] += ar <= b68
                a[2] += ar <= b95
                a[3] += ar <= sg
                a[4] += ar <= 1.96 * sg
                a[5] += b68
                a[6] += b95
                a[7] += 2 * b95 + (2 / A95) * max(ar - b95, 0.0)
                a[8] += 2 * b68 + (2 / A68) * max(ar - b68, 0.0)

pooled = [a for a in TICKERS if a not in POOL_EXCLUDE and a in data]


def pooled_line(m, h):
    tot = [0] * 5
    ratios95, ratios68 = [], []
    for a in pooled:
        x = acc.get((m, h, a))
        base = acc.get(("exp", h, a))
        if not x or not base or x[0] < MIN_ASSET_N:
            continue
        for i in range(5):
            tot[i] += x[i]
        ratios95.append(x[7] / base[7])
        ratios68.append(x[8] / base[8])
    if not tot[0]:
        return "%-5s no samples" % m
    n = tot[0]
    g95 = math.exp(sum(math.log(r) for r in ratios95) / len(ratios95))
    g68 = math.exp(sum(math.log(r) for r in ratios68) / len(ratios68))
    wins = sum(1 for r in ratios95 if r < 1.0)
    w = "-" if m == "exp" else "%d/%d" % (wins, len(ratios95))
    return "%-5s%8d%6.1f%6.1f%6.1f%6.1f%7.3f%7.3f %5s" % (
        m, n, tot[1] / n * 100, tot[2] / n * 100, tot[3] / n * 100,
        tot[4] / n * 100, g95, g68, w)


log("market-lab range forecast  mode=" + MODE)
if MODE == "dev":
    log("dev data: samples whose target date is before " + HOLDOUT_START.isoformat())
else:
    log("HOLDOUT: samples from " + HOLDOUT_START.isoformat() + " onward")
log("pooled assets: " + " ".join(pooled))
log("excluded from pool: " + " ".join(sorted(POOL_EXCLUDE)))
log("")
log("cov68/cov95: % of real moves inside the calibrated band (target 68.3/95)")
log("raw68/raw95: same but with plain normal bands 1.00 and 1.96 sigma")
log("IS95x/IS68x: interval score vs exp model, geo-mean over assets (<1 better)")
log("wins: assets where model beats exp on IS95")
for h in HORIZONS:
    log("")
    log("== horizon %d days ==" % h)
    log("model      n cov68 cov95 raw68 raw95  IS95x  IS68x  wins")
    for m in MODELS:
        log(pooled_line(m, h))
SUMFILE.write_text("\n".join(lines) + "\n")

al = []
al.append("by asset, model=" + PRIMARY + ", half-width in % of log return")
al.append("hw68/hw95 = average band half-width; IS95x vs exp model")
al.append("asset ranges: " + "; ".join(
    "%s %s..%s" % (a, span[a][0], span[a][1]) for a in TICKERS if a in span))
for h in HORIZONS:
    al.append("")
    al.append("== horizon %d days ==" % h)
    al.append("asset        n cov68 cov95  hw68  hw95  IS95x")
    for a in TICKERS:
        x = acc.get((PRIMARY, h, a))
        base = acc.get(("exp", h, a))
        if not x or not base:
            al.append("%-8s -" % a)
            continue
        al.append("%-8s%6d%6.1f%6.1f%6.1f%6.1f%7.3f" % (
            a, x[0], x[1] / x[0] * 100, x[2] / x[0] * 100,
            x[5] / x[0] * 100, x[6] / x[0] * 100, x[7] / base[7]))
(OUT / ("range_" + MODE + "_by_asset.txt")).write_text("\n".join(al) + "\n")

with open(OUT / ("range_" + MODE + "_detail.csv"), "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["model", "h", "asset", "n", "in68", "in95", "raw68", "raw95",
                "sum_b68", "sum_b95", "sum_is95", "sum_is68"])
    for key in sorted(acc):
        w.writerow(list(key) + acc[key])

if MODE == "dev":
    nw = []
    nw.append("current bands, model=" + PRIMARY + ", from the last close")
    nw.append("68% and 95% = share of past moves that stayed inside")
    nw.append("price levels are lower / upper limit; not a direction call")
    for a in TICKERS:
        if a not in data:
            continue
        ds, cs, sig = data[a]
        n = len(cs)
        last = cs[-1]
        nw.append("")
        nw.append("%s  last close %.2f on %s  daily vol %.2f%%" % (
            a, last, ds[-1], sig[PRIMARY][-1] * 100))
        for h in HORIZONS:
            pool = []
            for t0 in range(POOL_START, n - h):
                r0 = abs(math.log(cs[t0 + h] / cs[t0]))
                pool.append(r0 / (sig[PRIMARY][t0] * math.sqrt(h)))
            pool.sort()
            if len(pool) < MINPOOL:
                nw.append("  %2dd: not enough history" % h)
                continue
            sg = sig[PRIMARY][-1] * math.sqrt(h)
            parts = []
            for p in (0.683, 0.95):
                b = quant(pool, p) * sg
                parts.append("%2d%%: %.2f-%.2f (%+.1f%%/%+.1f%%)" % (
                    round(p * 100), last * math.exp(-b), last * math.exp(b),
                    (math.exp(-b) - 1) * 100, (math.exp(b) - 1) * 100))
            nw.append("  %2dd  %s" % (h, "  ".join(parts)))
    (OUT / "range_now.txt").write_text("\n".join(nw) + "\n")
print("done")
