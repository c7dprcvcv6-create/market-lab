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
HS = [5, 10]
POOL_START = 60
MINPOOL = 250
START = POOL_START + MINPOOL + max(HS)
Z95 = 1.96
ZSTRICT = 3.0
STABLE = 0.03
TICKERS = ["BTC-USD", "ETH-USD", "BMNR", "COIN", "MSTR", "SPY", "QQQ",
           "NVDA", "TSLA", "AAPL", "MSFT", "JPM", "KO", "XOM", "GLD"]
POOL_EXCLUDE = {"BMNR"}
PRICES = pathlib.Path("data/prices")
OUT = pathlib.Path("data/results")
OUT.mkdir(parents=True, exist_ok=True)

if MODE not in ("dev", "holdout"):
    print("MODE must be dev or holdout")
    sys.exit(1)
SUMFILE = OUT / ("levels_" + MODE + ".txt")
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


def quant(pool, p):
    return pool[int(p * (len(pool) - 1))]


def run_path(side, E, W, T, hi, lo, t, H):
    d1 = None
    for d in range(1, H + 1):
        if side < 0 and lo[t + d] <= E:
            d1 = d
            break
        if side > 0 and hi[t + d] >= E:
            d1 = d
            break
    if d1 is None:
        return 0
    dw = db = None
    for d in range(d1, H + 1):
        if dw is None:
            if (side < 0 and lo[t + d] <= W) or (side > 0 and hi[t + d] >= W):
                dw = d
        if db is None and d > d1:
            if (side < 0 and hi[t + d] >= T) or (side > 0 and lo[t + d] <= T):
                db = d
    if dw is not None and (db is None or dw <= db):
        return 3
    if db is not None:
        return 2
    return 4


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
    if n < START + 30:
        log("too short for tests: " + tk)
    lr = [0.0] + [math.log(cl[i] / cl[i - 1]) for i in range(1, n)]
    sig = ewma_std(lr)
    sma = sma_list(cl, 200)
    data[tk] = (ds, cl, sig)
    if n < START + 30 or tk in POOL_EXCLUDE:
        continue
    for H in HS:
        sq = math.sqrt(H)
        pool = []
        for t in range(POOL_START + H, n - H):
            t0 = t - H
            bisect.insort(pool, abs(math.log(cl[t] / cl[t0])) / (sig[t0] * sq))
            if t < START or sma[t] is None:
                continue
            end = ds[t + H]
            if MODE == "dev":
                if end >= HOLDOUT_START:
                    break
                per = "train" if end < TRAIN_END else "val"
            else:
                if ds[t] < HOLDOUT_START:
                    continue
                per = "hold"
            sg = sig[t] * sq
            b68 = quant(pool, 0.683) * sg
            b95 = quant(pool, 0.95) * sg
            C = cl[t]
            trend = 1 if C > sma[t] else 0
            lo_s, hi_s = span.get(tk, (ds[t], ds[t]))
            span[tk] = (min(lo_s, ds[t]), max(hi_s, end))
            for side in (-1, 1):
                E = C * math.exp(side * b68)
                W = C * math.exp(side * b95)
                half = C * math.exp(side * b68 / 2)
                code = run_path(side, E, W, half, hi, lo, t, H)
                full = run_path(side, E, W, C, hi, lo, t, H)
                key = (per, H, side, trend, tk)
                s = stats.setdefault(key, [0, 0, 0, 0, 0, 0])
                s[0] += 1
                if code:
                    s[1] += 1
                    if code == 2:
                        s[2] += 1
                    elif code == 3:
                        s[3] += 1
                    else:
                        s[4] += 1
                    if full == 2:
                        s[5] += 1

pooled = [a for a in TICKERS if a in data and a not in POOL_EXCLUDE
          and len(data[a][1]) >= START + 30]


def agg(pers, H, side, trends=(0, 1), assets=None):
    tot = [0, 0, 0, 0, 0, 0]
    for a in (assets or pooled):
        for per in pers:
            for tr in trends:
                s = stats.get((per, H, side, tr, a))
                if s:
                    for i in range(6):
                        tot[i] += s[i]
    return tot


def rates(s):
    n, tch, bo, wr, ne, bf = s
    if n == 0 or tch == 0:
        return None
    return (tch / n, bo / tch, wr / tch, ne / tch, bf / tch)


def fmt(s):
    r = rates(s)
    if not r:
        return "no samples"
    return "%8d %6.1f %6.1f %6.1f %8.1f %6.1f %8d" % (
        s[0], r[0] * 100, r[1] * 100, r[2] * 100, r[3] * 100, r[4] * 100, s[1])


SIDE = {-1: "ลงแตะ", 1: "ขึ้นแตะ"}
log("market-lab levels test  mode=" + MODE)
if MODE == "dev":
    log("train: windows ending before " + TRAIN_END.isoformat()
        + "   val: " + TRAIN_END.isoformat() + " to "
        + HOLDOUT_START.isoformat())
else:
    log("HOLDOUT: windows from " + HOLDOUT_START.isoformat() + " onward")
log("pooled: " + " ".join(pooled))
log("entry = edge of the 68% band, wrong-way = edge of the 95% band")
log("touch uses daily high/low. half = halfway back to the signal price")
log("half|touch = reaches halfway before the wrong-way level")
log("wrong|touch = hits wrong-way level first. neither = neither in window")
log("full = returns all the way to the signal price before the wrong-way level")
log("")
checks = []
for H in HS:
    for side in (-1, 1):
        log("== %d days, %s ==" % (H, SIDE[side]))
        log("period       n touch%  half% wrong% neither%  full%  n_touch")
        pers = ("train", "val") if MODE == "dev" else ("hold",)
        rr = {}
        for per in pers:
            s = agg((per,), H, side)
            log("%-8s %s" % (per, fmt(s)))
            rr[per] = rates(s)
        if MODE == "dev" and rr.get("train") and rr.get("val"):
            d = [(rr["val"][i] - rr["train"][i]) for i in range(4)]
            ok = all(abs(x) <= STABLE for x in d)
            log("val-train  %+6.1f %+6.1f %+6.1f %+8.1f   %s" % (
                d[0] * 100, d[1] * 100, d[2] * 100, d[3] * 100,
                "STABLE" if ok else "DRIFT"))
            checks.append(("stable %dd %s" % (H, SIDE[side]), ok))
        if MODE == "dev":
            zs = {}
            for per in pers:
                u = agg((per,), H, side, trends=(1,))
                dn = agg((per,), H, side, trends=(0,))
                ru, rd = rates(u), rates(dn)
                if ru and rd:
                    ne1, ne2 = max(u[1] / H, 1.0), max(dn[1] / H, 1.0)
                    se = math.sqrt(ru[1] * (1 - ru[1]) / ne1
                                   + rd[1] * (1 - rd[1]) / ne2)
                    z = (ru[1] - rd[1]) / max(se, 1e-9)
                    zs[per] = z
                    log("trend %-5s half|touch above SMA200 %.1f%% (n=%d)"
                        " below %.1f%% (n=%d) z=%+.1f" % (
                            per, ru[1] * 100, u[1], rd[1] * 100, dn[1], z))
            if "train" in zs and "val" in zs:
                info = (abs(zs["val"]) >= ZSTRICT
                        and zs["val"] * zs["train"] > 0)
                checks.append(("trend info %dd %s" % (H, SIDE[side]), info))
        log("")

if MODE == "dev":
    log("== criteria (set before running) ==")
    for name, ok in checks:
        log("%-22s %s" % (name, "PASS" if ok else "FAIL"))
SUMFILE.write_text("\n".join(lines) + "\n")

with open(OUT / ("levels_" + MODE + "_detail.csv"), "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["period", "H", "side", "trend", "asset", "n", "touch",
                "bounce", "wrong", "neither"])
    for key in sorted(stats):
        w.writerow(list(key) + stats[key])

if MODE == "dev":
    def price(x):
        return "{:,.0f}".format(x) if x >= 1000 else "{:,.2f}".format(x)

    nw = ["levels now, from the last close (statistics, not advice)",
          "odds come from pooled history before 2023-10-24"]
    for tk in TICKERS:
        if tk not in data:
            continue
        ds, cl, sig = data[tk]
        n = len(cl)
        C = cl[-1]
        nw.append("")
        nw.append("%s %s (close %s)" % (tk.replace("-USD", ""), price(C),
                                        ds[-1]))
        for H in HS:
            sq = math.sqrt(H)
            pool = sorted(abs(math.log(cl[t0 + H] / cl[t0])) / (sig[t0] * sq)
                          for t0 in range(POOL_START, n - H))
            if len(pool) < MINPOOL:
                nw.append("  %d days: not enough history" % H)
                continue
            sg = sig[-1] * sq
            b68 = quant(pool, 0.683) * sg
            b95 = quant(pool, 0.95) * sg
            nw.append("  %d days" % H)
            for side in (-1, 1):
                r = rates(agg(("train", "val"), H, side))
                if not r:
                    continue
                E = C * math.exp(side * b68)
                W = C * math.exp(side * b95)
                word = "ลงแตะ" if side < 0 else "ขึ้นแตะ"
                back = "เด้งกลับ" if side < 0 else "ย่อกลับ"
                mid = C * math.exp(side * b68 / 2)
                nw.append("    ถ้า%s %s (โอกาสแตะ %.0f%%)" % (
                    word, price(E), r[0] * 100))
                nw.append("      หลังแตะ: %sถึงครึ่งทาง %s ก่อน = %.0f%%"
                          " | ไปถึง %s ก่อน (ผิดทาง) = %.0f%%"
                          " | ไม่ถึงทั้งสอง = %.0f%%" % (
                              back, price(mid), r[1] * 100, price(W),
                              r[2] * 100, r[3] * 100))
                nw.append("      %sถึงราคาเดิม %s เต็มทางก่อนผิดทาง = %.0f%%" % (
                    back, price(C), r[4] * 100))
    (OUT / "levels_now.txt").write_text("\n".join(nw) + "\n")
print("done")
