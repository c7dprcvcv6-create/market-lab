import csv
import datetime as dt
import json
import math
import os
import pathlib
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

TICKERS = ["BTC-USD", "ETH-USD", "BMNR", "COIN", "MSTR", "SPY", "QQQ",
           "NVDA", "TSLA", "AAPL", "MSFT", "JPM", "KO", "XOM", "GLD"]
CRYPTO = {"BTC-USD", "ETH-USD"}
HORIZONS = [1, 3, 5, 21]
MODELS = ["exp", "ewma"]
DISPLAY = {1: "ewma", 3: "ewma", 5: "ewma", 21: "exp"}
POOL_START = 60
MINPOOL = 250
A68 = 0.317
A95 = 0.05
PRICES = pathlib.Path("data/prices")
PRED = pathlib.Path("data/predictions")
LOG = PRED / "log.csv"
RES = PRED / "results.csv"
SUMMARY = pathlib.Path("data/latest_summary.txt")
UA = {"User-Agent": "Mozilla/5.0 (market-lab)"}
LOG_COLS = ["id", "created_utc", "asset", "as_of", "h", "model",
            "last_close", "sigma_daily", "b68", "b95", "pool_n"]
RES_COLS = ["id", "resolved_utc", "asset", "as_of", "target_date", "h",
            "model", "r_log", "b68", "b95", "in68", "in95", "is68", "is95",
            "sigma_daily"]


def utcnow():
    return dt.datetime.now(dt.timezone.utc)


def http_get(url, tries=3):
    last = (-1, "")
    for i in range(tries):
        req = urllib.request.Request(url, headers=UA)
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status, r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            last = (e.code, "")
        except Exception as e:
            last = (-1, type(e).__name__)
        time.sleep(3 * (i + 1))
    return last


def fetch_recent(tk):
    url = ("https://query1.finance.yahoo.com/v8/finance/chart/" + tk
           + "?range=6mo&interval=1d")
    status, body = http_get(url)
    if status != 200:
        raise RuntimeError("status " + str(status))
    r = json.loads(body)["chart"]["result"][0]
    ts = r["timestamp"]
    q = r["indicators"]["quote"][0]
    adj = None
    if r["indicators"].get("adjclose"):
        adj = r["indicators"]["adjclose"][0]["adjclose"]
    out = {}
    for i, s in enumerate(ts):
        c = q["close"][i]
        if c is None:
            continue
        d = dt.datetime.fromtimestamp(s, dt.timezone.utc).date()
        a = adj[i] if adj and adj[i] is not None else c
        out[d] = [q["open"][i], q["high"][i], q["low"][i], c, a, q["volume"][i]]
    return out


def is_complete(tk, d, now):
    if d < now.date():
        return True
    if d > now.date():
        return False
    if tk in CRYPTO:
        return False
    return now.hour * 60 + now.minute >= 21 * 60 + 30


def num(x):
    try:
        return float(x)
    except (ValueError, TypeError):
        return None


def read_prices(tk):
    rows = {}
    with open(PRICES / (tk + ".csv")) as f:
        for row in csv.DictReader(f):
            try:
                d = dt.date.fromisoformat(row["date"])
            except ValueError:
                continue
            rows[d] = [num(row["open"]), num(row["high"]), num(row["low"]),
                       num(row["close"]), num(row["adjclose"]),
                       num(row["volume"])]
    return rows


def write_prices(tk, rows):
    with open(PRICES / (tk + ".csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "open", "high", "low", "close", "adjclose",
                    "volume"])
        for d in sorted(rows):
            w.writerow([d.isoformat()] + rows[d])


def merge(stored, fresh):
    common = [d for d in stored if d in fresh]
    fa, fc = [], []
    for d in common:
        s, f = stored[d], fresh[d]
        if s[4] and f[4] and s[4] > 0 and f[4] > 0:
            fa.append(f[4] / s[4])
        if s[3] and f[3] and s[3] > 0 and f[3] > 0:
            fc.append(f[3] / s[3])
    if len(fa) >= 5:
        k = statistics.median(fa)
        if abs(k - 1) > 1e-4:
            for d in stored:
                if stored[d][4]:
                    stored[d][4] *= k
    if len(fc) >= 5:
        k = statistics.median(fc)
        if abs(k - 1) > 1e-4:
            for d in stored:
                for i in range(4):
                    if stored[d][i]:
                        stored[d][i] *= k
    last = max(stored) if stored else dt.date(1900, 1, 1)
    added = 0
    for d in sorted(fresh):
        if d > last:
            stored[d] = fresh[d]
            added += 1
    return added


def make_series(rows):
    ds = sorted(rows)
    cs = []
    for d in ds:
        v = rows[d]
        cs.append(v[4] if v[4] else v[3])
    return ds, cs


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


def quant(pool, p):
    return pool[int(p * (len(pool) - 1))]


def band_pack(cs):
    n = len(cs)
    lr = [0.0] + [math.log(cs[i] / cs[i - 1]) for i in range(1, n)]
    sig = {"exp": exp_std(lr), "ewma": ewma_std(lr)}
    out = {}
    for h in HORIZONS:
        sq = math.sqrt(h)
        pools = {m: [] for m in MODELS}
        for t0 in range(POOL_START, n - h):
            r0 = abs(math.log(cs[t0 + h] / cs[t0]))
            for m in MODELS:
                pools[m].append(r0 / (sig[m][t0] * sq))
        for m in MODELS:
            pool = sorted(pools[m])
            if len(pool) < MINPOOL or sig[m][-1] is None:
                continue
            sg = sig[m][-1] * sq
            out[(h, m)] = (sig[m][-1], quant(pool, 0.683) * sg,
                           quant(pool, 0.95) * sg, len(pool))
    return out


def read_csv(path):
    if not path.exists():
        return []
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def append_csv(path, cols, rows):
    new = not path.exists()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(cols)
        for r in rows:
            w.writerow(r)


def hw(b):
    return (math.exp(b) - math.exp(-b)) / 2 * 100


def fp(x):
    return "%.1f" % x if x < 10 else "%.0f" % x


def short(tk):
    return tk.replace("-USD", "")


def fmt_price(x):
    return "{:,.0f}".format(x) if x >= 1000 else "{:,.2f}".format(x)


def send_telegram(text):
    token = os.environ.get("TELEGRAM_TOKEN", "").strip()
    chat = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat:
        print("telegram secrets missing, message not sent")
        return False
    body = urllib.parse.urlencode(
        {"chat_id": chat, "text": text[:4000]}).encode()
    req = urllib.request.Request(
        "https://api.telegram.org/bot" + token + "/sendMessage", data=body)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            print("telegram status", r.status)
            return r.status == 200
    except urllib.error.HTTPError as e:
        print("telegram error", e.code)
    except Exception as e:
        print("telegram error", type(e).__name__)
    return False


def scoreboard(res_rows):
    g = {}
    for r in res_rows:
        k = (int(r["h"]), r["model"])
        x = g.setdefault(k, [0, 0, 0])
        x[0] += 1
        x[1] += int(r["in68"])
        x[2] += int(r["in95"])
    lines = []
    for h in HORIZONS:
        m = DISPLAY[h]
        x = g.get((h, m))
        if not x:
            lines.append("%dd: ยังไม่มีผลที่ครบกำหนด" % h)
        elif x[0] < 30:
            lines.append("%dd: สะสมอยู่ (n=%d)" % (h, x[0]))
        else:
            lines.append("%dd: n=%d ครอบคลุม 68%%=%.0f%% 95%%=%.0f%%" % (
                h, x[0], x[1] / x[0] * 100, x[2] / x[0] * 100))
    return lines


def main():
    if os.environ.get("TEST", "").strip().lower() == "true":
        send_telegram("market-lab: test message OK")
        return
    now = utcnow()
    errors = []
    series = {}
    prices = {}
    for tk in TICKERS:
        fresh = {}
        try:
            raw = fetch_recent(tk)
            fresh = {d: v for d, v in raw.items() if is_complete(tk, d, now)}
        except Exception as e:
            errors.append(short(tk) + " " + str(e)[:40])
        stored = read_prices(tk)
        if fresh:
            merge(stored, fresh)
            write_prices(tk, stored)
        prices[tk] = stored
        series[tk] = make_series(stored)
        time.sleep(1.5)
    if len(errors) == len(TICKERS):
        send_telegram("market-lab: ดึงราคาไม่ได้ทั้งหมด " + now.isoformat()[:16])
        print("all downloads failed")
        sys.exit(1)

    log_rows = read_csv(LOG)
    known = set(r["id"] for r in log_rows)
    done = set(r["id"] for r in read_csv(RES))
    created = now.isoformat(timespec="seconds")

    new_res = []
    for r in log_rows:
        if r["id"] in done:
            continue
        ds, cs = series.get(r["asset"], ([], []))
        h = int(r["h"])
        try:
            i = ds.index(dt.date.fromisoformat(r["as_of"]))
        except ValueError:
            continue
        if i + h > len(cs) - 1:
            continue
        rl = math.log(cs[i + h] / cs[i])
        ar = abs(rl)
        b68, b95 = float(r["b68"]), float(r["b95"])
        new_res.append([
            r["id"], created, r["asset"], r["as_of"], ds[i + h].isoformat(),
            h, r["model"], "%.6f" % rl, r["b68"], r["b95"],
            int(ar <= b68), int(ar <= b95),
            "%.6f" % (2 * b68 + (2 / A68) * max(ar - b68, 0.0)),
            "%.6f" % (2 * b95 + (2 / A95) * max(ar - b95, 0.0)),
            r["sigma_daily"]])
    if new_res:
        append_csv(RES, RES_COLS, new_res)

    new_log = []
    shown = []
    for tk in TICKERS:
        ds, cs = series[tk]
        if len(cs) < POOL_START + MINPOOL + 25:
            errors.append(short(tk) + " ข้อมูลสั้นเกินไป")
            continue
        if (now.date() - ds[-1]).days > 7:
            errors.append(short(tk) + " ข้อมูลเก่าเกิน 7 วัน")
            continue
        pack = band_pack(cs)
        as_of = ds[-1].isoformat()
        last_close = prices[tk][ds[-1]][3]
        made = False
        parts = []
        for h in HORIZONS:
            for m in MODELS:
                if (h, m) not in pack:
                    continue
                pid = "%s|%s|%d|%s" % (tk, as_of, h, m)
                sg, b68, b95, pn = pack[(h, m)]
                if pid not in known:
                    new_log.append([pid, created, tk, as_of, h, m,
                                    "%.4f" % last_close, "%.6f" % sg,
                                    "%.6f" % b68, "%.6f" % b95, pn])
                    known.add(pid)
                    made = True
                if m == DISPLAY[h]:
                    star = "*" if h == 21 else ""
                    parts.append("%dd%s %s/%s" % (h, star, fp(hw(b68)), fp(hw(b95))))
        if made:
            shown.append("%s %s | %s" % (short(tk), fmt_price(last_close),
                                         " | ".join(parts)))
    if new_log:
        append_csv(LOG, LOG_COLS, new_log)

    misses = []
    for r in new_res:
        if r[5] == 1 and r[6] == "ewma" and r[11] == 0:
            misses.append("%s ขยับ %+.1f%% (ช่วง 95%% ±%.1f%%)" % (
                short(r[2]), (math.exp(float(r[7])) - 1) * 100,
                hw(float(r[9]))))

    lines = ["market-lab " + now.date().isoformat(),
             "ช่วงที่น่าจะขยับ ±% (68%/95%) ไม่ใช่การทายทิศ"]
    lines += shown
    if shown:
        lines.append("* 21 วัน ใช้ค่าระยะยาว ยังไม่พิสูจน์")
    if misses:
        lines.append("")
        lines.append("นอกช่วง 95% (1 วัน): " + "; ".join(misses))
    lines.append("")
    lines.append("ผลสะสมที่ตรวจแล้ว:")
    lines += scoreboard(read_csv(RES))
    if errors:
        lines.append("")
        lines.append("ข้อควรดู: " + "; ".join(errors))
    text = "\n".join(lines)
    print(text)
    if shown or new_res or errors:
        SUMMARY.write_text(text + "\n")
        send_telegram(text)
    else:
        print("nothing new, no message sent")


if __name__ == "__main__":
    main()
