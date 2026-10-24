import bisect
import csv
import datetime as dt
import math
import os
import pathlib
import random
import sys

MODE = (os.environ.get("MODE") or "dev").strip()
HOLDOUT_START = dt.date(2023, 10, 24)
HORIZONS = [1, 3, 5, 21]
WARMUP = 200
Z95 = 1.96
ZSTRICT = 3.0
TICKERS = ["BTC-USD", "ETH-USD", "BMNR", "COIN", "MSTR", "SPY", "QQQ",
           "NVDA", "TSLA", "AAPL", "MSFT", "JPM", "KO", "XOM", "GLD"]
POOL_EXCLUDE = {"BMNR"}
SIGS = ["sma", "rsi", "macd", "mom", "brk"]
BASES = ["up", "prev", "rnd"]
ALL = SIGS + BASES
PRICES = pathlib.Path("data/prices")
OUT = pathlib.Path("data/results")
OUT.mkdir(parents=True, exist_ok=True)

if MODE not in ("dev", "holdout"):
    print("MODE must be dev or holdout")
    sys.exit(1)
SUMFILE = OUT / ("backtest_" + MODE + ".txt")
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


def sma_list(c, p):
    out = [None] * len(c)
    s = 0.0
    for i, v in enumerate(c):
        s += v
        if i >= p:
            s -= c[i - p]
        if i >= p - 1:
            out[i] = s / p
    return out


def ema_list(x, span):
    a = 2.0 / (span + 1)
    out = []
    e = None
    for v in x:
        e = v if e is None else a * v + (1 - a) * e
        out.append(e)
    return out


def rsi_list(c, p=14):
    n = len(c)
    out = [None] * n
    if n <= p:
        return out
    gain = loss = 0.0
    for i in range(1, p + 1):
        d = c[i] - c[i - 1]
        gain += max(d, 0.0)
        loss += max(-d, 0.0)
    gain /= p
    loss /= p
    out[p] = 100.0 if loss == 0 else 100.0 - 100.0 / (1 + gain / loss)
    for i in range(p + 1, n):
        d = c[i] - c[i - 1]
        gain = (gain * (p - 1) + max(d, 0.0)) / p
        loss = (loss * (p - 1) + max(-d, 0.0)) / p
        out[i] = 100.0 if loss == 0 else 100.0 - 100.0 / (1 + gain / loss)
    return out


def vol_list(c, p=20):
    n = len(c)
    lr = [0.0] + [math.log(c[i] / c[i - 1]) for i in range(1, n)]
    out = [None] * n
    for i in range(p, n):
        w = lr[i - p + 1:i + 1]
        m = sum(w) / p
        out[i] = math.sqrt(sum((x - m) ** 2 for x in w) / (p - 1))
    return out


def regimes(vol):
    out = [None] * len(vol)
    hist = []
    for t, v in enumerate(vol):
        if v is None:
            continue
        bisect.insort(hist, v)
        out[t] = "hi" if v > hist[len(hist) // 2] else "lo"
    return out


def build_signals(c, seed):
    n = len(c)
    s50 = sma_list(c, 50)
    s200 = sma_list(c, 200)
    rsi = rsi_list(c)
    ema12 = ema_list(c, 12)
    ema26 = ema_list(c, 26)
    macd = [a - b for a, b in zip(ema12, ema26)]
    sig9 = ema_list(macd, 9)
    rnd = random.Random(seed)
    P = {k: [0] * n for k in ALL}
    for t in range(n):
        if s200[t] is not None:
            P["sma"][t] = 1 if s50[t] > s200[t] else -1
        if rsi[t] is not None:
            if rsi[t] < 30:
                P["rsi"][t] = 1
            elif rsi[t] > 70:
                P["rsi"][t] = -1
        P["macd"][t] = 1 if macd[t] > sig9[t] else -1
        if t >= 20:
            P["mom"][t] = 1 if c[t] > c[t - 20] else -1
            prior = c[t - 20:t]
            if c[t] > max(prior):
                P["brk"][t] = 1
            elif c[t] < min(prior):
                P["brk"][t] = -1
        if t >= 1:
            r1 = c[t] / c[t - 1] - 1.0
            P["prev"][t] = 1 if r1 > 0 else (-1 if r1 < 0 else 0)
        P["up"][t] = 1
        P["rnd"][t] = rnd.choice((-1, 1))
    return P


stats = {}
runs = {}
span = {}
for tk in TICKERS:
    try:
        ds, cs = load(tk)
    except FileNotFoundError:
        log("missing file: " + tk)
        continue
    n = len(cs)
    if n < WARMUP + 30:
        log("too short: " + tk)
        continue
    P = build_signals(cs, sum(ord(ch) for ch in tk))
    vol = vol_list(cs)
    reg = regimes(vol)
    for h in HORIZONS:
        last = {k: 0 for k in ALL}
        for t in range(WARMUP, n - h):
            if MODE == "dev":
                if ds[t + h] >= HOLDOUT_START:
                    break
            elif ds[t] < HOLDOUT_START:
                continue
            r = cs[t + h] / cs[t] - 1.0
            m = vol[t] * math.sqrt(h)
            rg = reg[t]
            lo, hi = span.get(tk, (ds[t], ds[t]))
            span[tk] = (min(lo, ds[t]), max(hi, ds[t + h]))
            for k in ALL:
                p = P[k][t]
                if p == 0:
                    last[k] = 0
                    continue
                s = stats.setdefault((k, h, tk, rg), [0, 0, 0, 0.0, 0.0])
                s[0] += 1
                if (p > 0 and r > 0) or (p < 0 and r < 0):
                    s[1] += 1
                if r > 0:
                    s[2] += 1
                s[3] += abs(p * m - r)
                s[4] += abs(r)
                q = runs.setdefault((k, h, tk), [0, 0])
                q[0] += 1
                if last[k] != p:
                    q[1] += 1
                last[k] = p


def pool(k, h, assets, regs=("lo", "hi")):
    tot = [0, 0, 0, 0.0, 0.0]
    for a in assets:
        for rg in regs:
            s = stats.get((k, h, a, rg))
            if s:
                for i in range(5):
                    tot[i] += s[i]
    return tot


def neff(k, h, assets):
    tot = 0.0
    for a in assets:
        q = runs.get((k, h, a))
        if q and q[1] > 0:
            tot += q[0] / max(h, q[0] / q[1])
    return tot


def line(k, h, assets):
    n, hits, ups, err, err0 = pool(k, h, assets)
    if n == 0:
        return "%-5s no samples" % k
    p = hits / n
    b = ups / n
    edge = p - b
    ne = max(neff(k, h, assets), 1.0)
    se = math.sqrt(max(p * (1 - p), 1e-9) / ne)
    mae = err / err0 if err0 else float("nan")
    if k == "up":
        ci, v = "   -", "-"
    else:
        ci = "%5.1f" % (Z95 * se * 100)
        v = "+" if edge > ZSTRICT * se else ("-" if edge < -ZSTRICT * se else "=")
    return "%-5s%8d%7.0f%6.1f%6.1f%+6.1f%s  %s %5.2f" % (
        k, n, ne, p * 100, b * 100, edge * 100, ci, v, mae)


pooled = [a for a in TICKERS if a not in POOL_EXCLUDE]
log("market-lab backtest  mode=" + MODE)
if MODE == "dev":
    log("dev data: samples whose target date is before " + HOLDOUT_START.isoformat())
else:
    log("HOLDOUT: samples from " + HOLDOUT_START.isoformat() + " onward")
log("pooled assets: " + " ".join(pooled))
log("excluded from pool: " + " ".join(sorted(POOL_EXCLUDE)) + " (short history)")
log("")
log("legend: n=predictions  neff=independent bets  hit=%correct")
log("base=%up on same days  edge=hit-base (points)  +-=95%CI half width")
log("v: + better, - worse, = no proof (strict z=3, 20 tests)")
log("MAEx: move error vs predicting 0; compare with rnd row")
for h in HORIZONS:
    log("")
    log("== horizon %d days ==" % h)
    log("sig       n   neff   hit  base  edge   +-  v  MAEx")
    for k in ALL:
        log(line(k, h, pooled))

log("")
log("== by volatility regime (edge pts, n) ==")
for h in HORIZONS:
    log("-- horizon %d --" % h)
    for k in SIGS:
        parts = []
        for rg in ("lo", "hi"):
            n, hits, ups, e, e0 = pool(k, h, pooled, (rg,))
            if n:
                parts.append("%s %+5.1f n=%d" % (rg, (hits - ups) / n * 100, n))
            else:
                parts.append("%s -" % rg)
        log("%-5s %s" % (k, "   ".join(parts)))
SUMFILE.write_text("\n".join(lines) + "\n")

al = []
al.append("by asset: edge in points vs always-up on same days")
al.append("cells with fewer than 100 predictions show -")
al.append("asset ranges: " + "; ".join(
    "%s %s..%s" % (a, span[a][0], span[a][1]) for a in TICKERS if a in span))
for h in HORIZONS:
    al.append("")
    al.append("== horizon %d days ==" % h)
    al.append("asset     n   up%  " + "  ".join("%5s" % k for k in SIGS))
    for a in TICKERS:
        n, hits, ups, e, e0 = pool("up", h, [a])
        if not n:
            al.append("%-8s -" % a)
            continue
        cells = []
        for k in SIGS:
            nn, hh, uu, e1, e2 = pool(k, h, [a])
            ok = nn >= 100
            cells.append("%5s" % ("%+.1f" % ((hh - uu) / nn * 100) if ok else "-"))
        al.append("%-8s%5d%6.1f  %s" % (a, n, ups / n * 100, "  ".join(cells)))
(OUT / ("backtest_" + MODE + "_by_asset.txt")).write_text("\n".join(al) + "\n")

with open(OUT / ("backtest_" + MODE + "_detail.csv"), "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["sig", "h", "asset", "regime", "n", "hits", "ups", "err", "err0"])
    for key in sorted(stats):
        w.writerow(list(key) + stats[key])
print("done")
