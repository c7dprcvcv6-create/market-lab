import bisect
import csv
import datetime as dt
import math
import os
import pathlib
import sys

MODE = (os.environ.get("MODE") or "dev").strip()
HOLDOUT_START = dt.date(2023, 10, 24)
TRAIN_END = dt.date(2012, 1, 1)
H = 5
POOL_START = 60
MINPOOL = 250
START = POOL_START + MINPOOL + 2 * H
STOPS = [0.5, 1.0]
KS = [1, 2, 3]
STYLES = ["now", "dip"]
ZSTRICT = 3.0
TICKERS = ["BTC-USD", "ETH-USD", "BMNR", "COIN", "MSTR", "SPY", "QQQ",
           "NVDA", "TSLA", "AAPL", "MSFT", "JPM", "KO", "XOM", "GLD"]
POOL_EXCLUDE = {"BMNR"}
PRICES = pathlib.Path("data/prices")
OUT = pathlib.Path("data/results")
OUT.mkdir(parents=True, exist_ok=True)

if MODE not in ("dev", "holdout"):
    print("MODE must be dev or holdout")
    sys.exit(1)
SUMFILE = OUT / ("rr_" + MODE + ".txt")
if MODE == "holdout" and SUMFILE.exists():
    print("holdout already used. delete the result file to run again")
    sys.exit(1)

lines = []


def log(s=""):
    print(s)
    lines.append(s)


def num(x):
    try:
        return float(x)
    except (ValueError, TypeError):
        return None


def load(t):
    ds, cl, hi, lo = [], [], [], []
    with open(PRICES / (t + ".csv")) as f:
        for row in csv.DictReader(f):
            c = num(row["close"])
            a = num(row["adjclose"])
            if a is None or a <= 0:
                a = c
            if a is None or a <= 0:
                continue
            k = a / c if c and c > 0 else 1.0
            h_ = num(row["high"])
            l_ = num(row["low"])
            h_ = h_ * k if h_ and h_ > 0 else a
            l_ = l_ * k if l_ and l_ > 0 else a
            ds.append(dt.date.fromisoformat(row["date"]))
            cl.append(a)
            hi.append(max(h_, a))
            lo.append(min(l_, a))
    return ds, cl, hi, lo


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


def quant(pool, p):
    return pool[int(p * (len(pool) - 1))]


def simulate(entry, stop, target, hi, lo, cl, a, b):
    for j in range(a, b + 1):
        if lo[j] <= stop:
            return "S", stop / entry - 1
        if hi[j] >= target:
            return "T", target / entry - 1
    return "N", cl[b] / entry - 1


stats = {}
span = {}
data = {}
for tk in TICKERS:
    try:
        ds, cl, hi, lo = load(tk)
    except FileNotFoundError:
        log("missing file: " + tk)
        continue
    n = len(cl)
    lr = [0.0] + [math.log(cl[i] / cl[i - 1]) for i in range(1, n)]
    sig = ewma_std(lr)
    data[tk] = (ds, cl, sig)
    if n < START + 30 or tk in POOL_EXCLUDE:
        continue
    sq = math.sqrt(H)
    pool = []
    for t in range(POOL_START + H, n - 2 * H):
        t0 = t - H
        bisect.insort(pool, abs(math.log(cl[t] / cl[t0])) / (sig[t0] * sq))
        if t < START:
            continue
        end = ds[t + 2 * H]
        if MODE == "dev":
            if end >= HOLDOUT_START:
                break
            per = "train" if end < TRAIN_END else "val"
        else:
            if ds[t] < HOLDOUT_START:
                continue
            per = "hold"
        b68 = quant(pool, 0.683) * sig[t] * sq
        C = cl[t]
        lo_s, hi_s = span.get(tk, (ds[t], ds[t]))
        span[tk] = (min(lo_s, ds[t]), max(hi_s, end))
        E = C * math.exp(-b68)
        d1 = None
        for d in range(1, H + 1):
            if lo[t + d] <= E:
                d1 = d
                break
        for s in STOPS:
            risk = 1 - math.exp(-s * b68)
            for k in KS:
                for style in STYLES:
                    if style == "now":
                        entry = C
                        a, b = t + 1, t + H
                        pre = None
                    else:
                        if d1 is None:
                            continue
                        entry = E
                        a, b = t + d1 + 1, t + d1 + H
                        pre = t + d1
                    stop = entry * (1 - risk)
                    target = entry * (1 + k * risk)
                    if pre is not None and lo[pre] <= stop:
                        res, pnl = "S", stop / entry - 1
                    else:
                        res, pnl = simulate(entry, stop, target, hi, lo, cl, a, b)
                    r = pnl / risk
                    key = (per, style, s, k, tk)
                    st = stats.setdefault(key, [0, 0, 0, 0, 0.0, 0.0])
                    st[0] += 1
                    st[{"T": 1, "S": 2, "N": 3}[res]] += 1
                    st[4] += r
                    st[5] += r * r

pooled = [a for a in TICKERS if a in data and a not in POOL_EXCLUDE
          and len(data[a][1]) >= START + 30]


def agg(per, style, s, k, assets=None):
    tot = [0, 0, 0, 0, 0.0, 0.0]
    for a in (assets or pooled):
        x = stats.get((per, style, s, k, a))
        if x:
            for i in range(6):
                tot[i] += x[i]
    return tot


def summ(x):
    n = x[0]
    if n < 30:
        return None
    m = x[4] / n
    var = max(x[5] / n - m * m, 1e-12)
    se = math.sqrt(var / max(n / H, 1.0))
    return (n, x[1] / n, x[2] / n, x[3] / n, m, se)


log("market-lab risk/reward test  mode=" + MODE)
if MODE == "dev":
    log("train: windows ending before " + TRAIN_END.isoformat()
        + "   val: " + TRAIN_END.isoformat() + " to "
        + HOLDOUT_START.isoformat())
else:
    log("HOLDOUT: windows from " + HOLDOUT_START.isoformat() + " onward")
log("pooled: " + " ".join(pooled))
log("trade held at most %d days after entry. stop is checked before" % H)
log("target inside the same day (conservative). no fees")
log("stop distance = 0.5 or 1.0 times the 68% band. 1:k = target k times")
log("the stop distance. now = buy at the close. dip = buy when price falls")
log("to the lower edge of the 68% band (filled at that price)")
log("R = profit divided by the stop distance (stop = -1, target = +k)")
log("")
pers = ("train", "val") if MODE == "dev" else ("hold",)
checks = []
for s in STOPS:
    log("== stop %.1f x band ==" % s)
    log("rr   style period        n target%  stop% neither%   meanR   +-95%")
    for k in KS:
        res = {}
        for style in STYLES:
            for per in pers:
                m = summ(agg(per, style, s, k))
                res[(style, per)] = m
                if m:
                    log("1:%d  %-5s %-6s %8d %7.1f %6.1f %7.1f %+7.2f %7.2f" % (
                        k, style, per, m[0], m[1] * 100, m[2] * 100,
                        m[3] * 100, m[4], 1.96 * m[5]))
        if MODE == "dev":
            zs = {}
            for per in pers:
                a, b = res.get(("dip", per)), res.get(("now", per))
                if a and b:
                    zs[per] = (a[4] - b[4]) / math.sqrt(a[5] ** 2 + b[5] ** 2)
            if len(zs) == 2:
                log("1:%d  dip minus now: z train %+.1f  z val %+.1f" % (
                    k, zs["train"], zs["val"]))
                ok = zs["train"] >= ZSTRICT and zs["val"] >= ZSTRICT
                checks.append(("dip beats now s=%.1f 1:%d" % (s, k), ok))
            for style in STYLES:
                a, b = res.get((style, "train")), res.get((style, "val"))
                if a and b:
                    ok = (a[4] / a[5] >= ZSTRICT and b[4] / b[5] >= ZSTRICT)
                    checks.append(("%s positive s=%.1f 1:%d" % (style, s, k), ok))
    log("")
if MODE == "dev":
    log("== criteria (set before running) ==")
    for name, ok in checks:
        log("%-26s %s" % (name, "PASS" if ok else "FAIL"))
SUMFILE.write_text("\n".join(lines) + "\n")

with open(OUT / ("rr_" + MODE + "_detail.csv"), "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["period", "style", "stop", "k", "asset", "n", "target",
                "stopped", "neither", "sumR", "sumR2"])
    for key in sorted(stats):
        w.writerow(list(key) + stats[key])

if MODE == "dev":
    def price(x):
        return "{:,.0f}".format(x) if x >= 1000 else "{:,.2f}".format(x)

    nw = ["buy now, stop and target, from the last close",
          "(statistics for learning, not advice)",
          "odds from pooled history before 2023-10-24, 5-day window",
          "target = 2 times the risk. figures are share of trades"]
    for tk in TICKERS:
        if tk not in data:
            continue
        ds, cl, sig = data[tk]
        n = len(cl)
        sq = math.sqrt(H)
        pool = sorted(abs(math.log(cl[t0 + H] / cl[t0])) / (sig[t0] * sq)
                      for t0 in range(POOL_START, n - H))
        if len(pool) < MINPOOL:
            continue
        C = cl[-1]
        b68 = quant(pool, 0.683) * sig[-1] * sq
        nw.append("")
        nw.append("%s %s (close %s)" % (tk.replace("-USD", ""), price(C), ds[-1]))
        for s in STOPS:
            m = summ([sum(v) for v in zip(
                agg("train", "now", s, 2), agg("val", "now", s, 2))])
            if not m:
                continue
            risk = 1 - math.exp(-s * b68)
            nw.append("  stop %s (%.1f%%)  target %s (+%.1f%%)  reach target first %.0f%%"
                      " | stop first %.0f%% | neither %.0f%%" % (
                          price(C * (1 - risk)), -risk * 100,
                          price(C * (1 + 2 * risk)), 2 * risk * 100,
                          m[1] * 100, m[2] * 100, m[3] * 100))
    (OUT / "rr_now.txt").write_text("\n".join(nw) + "\n")
print("done")
