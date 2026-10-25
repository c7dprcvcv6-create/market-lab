import csv
import datetime as dt
import math
import os
import pathlib
import statistics
import sys

MODE = (os.environ.get("MODE") or "dev").strip()
HOLDOUT_START = dt.date(2023, 10, 24)
SPLIT = dt.date(2012, 1, 1)
REBAL = 21
MIN_BARS = 60
LAM = 0.94
TICKERS = ["BTC-USD", "ETH-USD", "BMNR", "COIN", "MSTR", "SPY", "QQQ",
           "NVDA", "TSLA", "AAPL", "MSFT", "JPM", "KO", "XOM", "GLD"]
TEST_EXCLUDE = {"BMNR"}
PRICES = pathlib.Path("data/prices")
OUT = pathlib.Path("data/results")
OUT.mkdir(parents=True, exist_ok=True)

if MODE not in ("dev", "holdout"):
    print("MODE must be dev or holdout")
    sys.exit(1)
SUMFILE = OUT / ("riskparity_" + MODE + ".txt")
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
    out = {}
    with open(PRICES / (t + ".csv")) as f:
        for row in csv.DictReader(f):
            a = num(row["adjclose"])
            if a is None or a <= 0:
                a = num(row["close"])
            if a is None or a <= 0:
                continue
            out[dt.date.fromisoformat(row["date"])] = a
    return out


raw = {}
for tk in TICKERS:
    try:
        raw[tk] = load(tk)
    except FileNotFoundError:
        log("missing file: " + tk)
cal = sorted(raw["SPY"])
if MODE == "dev":
    cal = [d for d in cal if d < HOLDOUT_START]
else:
    cal = [d for d in cal if d >= HOLDOUT_START]
full_cal = sorted(raw["SPY"])

px = {}
for tk, series in raw.items():
    last = None
    col = []
    for d in full_cal:
        if d in series:
            last = series[d]
        col.append(last)
    px[tk] = col

ret = {}
vol = {}
for tk, col in px.items():
    r = [None] * len(full_cal)
    for i in range(1, len(full_cal)):
        if col[i] and col[i - 1]:
            r[i] = math.log(col[i] / col[i - 1])
    ret[tk] = r
    v = [None] * len(full_cal)
    var = None
    cnt = 0
    for i in range(len(full_cal)):
        x = r[i]
        if x is None:
            continue
        cnt += 1
        if var is None:
            var = x * x
        else:
            var = LAM * var + (1 - LAM) * x * x
        if cnt >= MIN_BARS:
            v[i] = math.sqrt(max(var, 1e-12))
    vol[tk] = v

idx = {d: i for i, d in enumerate(full_cal)}


def weights(i, scheme, universe):
    av = [tk for tk in universe if vol[tk][i] is not None]
    if not av:
        return {}
    if scheme == "equal":
        return {tk: 1.0 / len(av) for tk in av}
    inv = {tk: 1.0 / vol[tk][i] for tk in av}
    z = sum(inv.values())
    return {tk: inv[tk] / z for tk in av}


def backtest(scheme, universe, lo, hi):
    days = [d for d in cal if lo <= d < hi]
    rets = []
    w = {}
    since = REBAL
    cnt_assets = []
    for k, d in enumerate(days):
        i = idx[d]
        if since >= REBAL or not w:
            w = weights(i, scheme, universe)
            since = 0
        since += 1
        if not w:
            continue
        x = 0.0
        for tk, wt in w.items():
            r = ret[tk][i]
            if r is not None:
                x += wt * (math.exp(r) - 1.0)
        rets.append(x)
        cnt_assets.append(len(w))
    return rets, cnt_assets


def metrics(rets):
    if len(rets) < 30:
        return None
    n = len(rets)
    mu = statistics.mean(rets)
    sd = statistics.pstdev(rets)
    growth = 1.0
    peak = 1.0
    mdd = 0.0
    for x in rets:
        growth *= 1 + x
        peak = max(peak, growth)
        mdd = min(mdd, growth / peak - 1)
    years = n / 252.0
    ann = growth ** (1 / years) - 1
    return {"ann": ann, "vol": sd * math.sqrt(252),
            "sharpe": mu * 252 / (sd * math.sqrt(252)) if sd else 0.0,
            "mdd": mdd, "worst": min(rets), "n": n}


universe = [t for t in TICKERS if t in px and t not in TEST_EXCLUDE]
log("market-lab risk balance test  mode=" + MODE)
log("monthly rebalance (every %d trading days), no fees, daily bars" % REBAL)
log("equal = same money in each asset. invvol = same risk in each asset")
log("(weight proportional to 1 / recent volatility). spy = 100% SPY")
log("assets: " + " ".join(universe))
log("note: the asset list was picked with hindsight, so absolute returns")
log("are too good. compare the weighting schemes, not the absolute level.")
log("")
if MODE == "dev":
    periods = [("1999-2011", cal[0], SPLIT), ("2012-2023", SPLIT, HOLDOUT_START),
               ("all dev", cal[0], HOLDOUT_START)]
else:
    periods = [("holdout", HOLDOUT_START, dt.date(2100, 1, 1))]
res = {}
for name, lo, hi in periods:
    log("== %s ==" % name)
    log("scheme   return/yr   vol/yr  sharpe  max drop   worst day")
    for scheme, uni in (("equal", universe), ("invvol", universe),
                        ("spy", ["SPY"])):
        sch = "equal" if scheme == "spy" else scheme
        rets, cnt = backtest(sch, uni, lo, hi)
        m = metrics(rets)
        res[(name, scheme)] = m
        if not m:
            log("%-8s no data" % scheme)
            continue
        log("%-8s %8.1f%% %7.1f%% %7.2f %8.1f%% %10.1f%%" % (
            scheme, m["ann"] * 100, m["vol"] * 100, m["sharpe"],
            m["mdd"] * 100, m["worst"] * 100))
    log("")

if MODE == "dev":
    log("== criteria (set before running) ==")
    for name in ("1999-2011", "2012-2023"):
        a, b = res.get((name, "equal")), res.get((name, "invvol"))
        if not a or not b:
            continue
        log("%s lower vol than equal: %s" % (
            name, "PASS" if b["vol"] < a["vol"] else "FAIL"))
        log("%s smaller max drop than equal: %s" % (
            name, "PASS" if b["mdd"] > a["mdd"] else "FAIL"))
        log("%s sharpe not worse by more than 0.05: %s" % (
            name, "PASS" if b["sharpe"] >= a["sharpe"] - 0.05 else "FAIL"))
SUMFILE.write_text("\n".join(lines) + "\n")

if MODE == "dev":
    i = len(full_cal) - 1
    w = weights(i, "invvol", [t for t in TICKERS if t in px])
    ew = weights(i, "equal", [t for t in TICKERS if t in px])
    nw = ["risk balance weights now (statistics, not advice)",
          "as of " + full_cal[-1].isoformat() + ", same risk in each asset",
          "asset      risk-balanced   equal money"]
    for tk in sorted(w, key=lambda k: -w[k]):
        nw.append("%-8s %10.1f%% %12.1f%%" % (
            tk.replace("-USD", ""), w[tk] * 100, ew.get(tk, 0) * 100))
    nw.append("")
    nw.append("daily volatility used (recent, weighted):")
    for tk in sorted(w, key=lambda k: -w[k]):
        nw.append("%-8s %.2f%%" % (tk.replace("-USD", ""), vol[tk][i] * 100))
    (OUT / "riskparity_now.txt").write_text("\n".join(nw) + "\n")
print("done")
