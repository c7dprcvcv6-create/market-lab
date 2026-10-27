import csv
import math
import pathlib
import sys

sys.path.insert(0, "src")
import daily

SIDES = (-1,)
LIMIT = 3300
RED = "🔴"
GREEN = "🟢"
MONTHS = ["ม.ค.", "ก.พ.", "มี.ค.", "เม.ย.", "พ.ค.", "มิ.ย.",
          "ก.ค.", "ส.ค.", "ก.ย.", "ต.ค.", "พ.ย.", "ธ.ค."]
DETAIL = pathlib.Path("data/results/levels_dev_detail.csv")
real_send = daily.send_telegram
START = ""


def price(x):
    return "{:,.0f}".format(x) if x >= 1000 else "{:,.2f}".format(x)


def thai_date(iso):
    y, m, d = iso.split("-")
    return "%d %s" % (int(d), MONTHS[int(m) - 1])


def odds5():
    acc = {}
    if not DETAIL.exists():
        return {}
    with open(DETAIL, newline="") as f:
        rd = csv.reader(f)
        next(rd, None)
        for r in rd:
            try:
                if int(r[1]) != 5:
                    continue
                side = int(r[2])
                vals = [int(x) for x in r[5:11]]
            except (ValueError, IndexError):
                continue
            x = acc.setdefault(side, [0, 0, 0, 0, 0, 0])
            for i, v in enumerate(vals):
                x[i] += v
    out = {}
    for side, x in acc.items():
        n, tch, half, wrong, neither, full = x
        if n and tch:
            out[side] = (tch / n, half / tch, wrong / tch, neither / tch,
                         full / tch)
    return out


def build(text):
    preview = "test message" in text
    rows = daily.read_csv(daily.LOG)
    if preview:
        log, res = rows, []
        day = "ตัวอย่าง"
    else:
        log = [r for r in rows if r["created_utc"] >= START]
        res = [r for r in daily.read_csv(daily.RES)
               if r["resolved_utc"] >= START]
        day = thai_date(START[:10])
    odds = odds5()
    head = ["market-lab %s (5 วัน)" % day,
            "ยังไม่พิสูจน์ ไม่ใช่คำแนะนำซื้อขาย"]
    blocks = []
    for tk in daily.TICKERS:
        r = next((x for x in reversed(log) if x["asset"] == tk
                  and x["model"] == "ewma" and x["h"] == "5"), None)
        if not r:
            continue
        last = float(r["last_close"])
        b68, b95 = float(r["b68"]), float(r["b95"])
        b = ["%s %s" % (daily.short(tk), price(last))]
        for side in SIDES:
            o = odds.get(side)
            if not o:
                continue
            E = last * math.exp(side * b68)
            W = last * math.exp(side * b95)
            M = last * math.exp(side * b68 / 2)
            if side < 0:
                g1, g2, loss = M / E - 1, last / E - 1, W / E - 1
                risk = (E - W) / (M - E)
                b.append("%s ซื้อ %s (รอลงถึง %.0f%%)" % (
                    GREEN, price(E), o[0] * 100))
                b.append("   ขาย ½ ที่ %s (%+.1f%%) โอกาส %.0f%% | ที่เหลือที่ %s (%+.1f%%) โอกาส %.0f%%" % (
                    price(M), g1 * 100, o[1] * 100, price(last), g2 * 100,
                    o[4] * 100))
                b.append("%s หลุด %s (%+.1f%%) ออก พัก 24 ชม. ไม่ถัวเฉลี่ย | โอกาส %.0f%% | เสี่ยง %.1f : กำไร 1" % (
                    RED, price(W), loss * 100, o[2] * 100, risk))
            else:
                g1, g2, loss = E / M - 1, E / last - 1, -(W / E - 1)
                risk = (W - E) / (E - M)
                b.append("%s ขาย %s (รอขึ้นถึง %.0f%%)" % (
                    GREEN, price(E), o[0] * 100))
                b.append("   ซื้อคืน ½ ที่ %s (%+.1f%%) โอกาส %.0f%% | ที่เหลือที่ %s (%+.1f%%) โอกาส %.0f%%" % (
                    price(M), g1 * 100, o[1] * 100, price(last), g2 * 100,
                    o[4] * 100))
                b.append("%s ทะลุ %s (%+.1f%%) ออก พัก 24 ชม. ไม่ถัวเฉลี่ย | โอกาส %.0f%% | เสี่ยง %.1f : กำไร 1" % (
                    RED, price(W), loss * 100, o[2] * 100, risk))
        blocks.append(b)
    tail = []
    miss = []
    for r in res:
        if r["h"] == "1" and r["model"] == "ewma" and r["in95"] == "0":
            v = (math.exp(float(r["r_log"])) - 1) * 100
            miss.append("%s %s %+.1f%%" % (
                GREEN if v > 0 else RED, daily.short(r["asset"]), v))
    if miss:
        tail.append("ขยับแรงผิดปกติเมื่อวาน: " + ", ".join(miss))
    allres = [r for r in daily.read_csv(daily.RES)
              if r["model"] == "ewma" and r["h"] == "1"]
    if len(allres) >= 30:
        ok = sum(int(r["in68"]) for r in allres)
        tail.append("ช่วงราคาที่ใช้ ตรวจจริง %d ครั้ง ถูก %.0f%% (ควรราว 68%%)" % (
            len(allres), ok / len(allres) * 100))
    for ln in text.split("\n"):
        if ln.startswith("ข้อควรดู:"):
            tail.append(ln)
    return head, blocks, tail


def patched(text):
    if "ดึงราคาไม่ได้" in text:
        return real_send(text)
    head, blocks, tail = build(text)
    msgs = []
    cur = "\n".join(head)
    for b in blocks + ([tail] if tail else []):
        piece = "\n\n" + "\n".join(b)
        if len(cur) + len(piece) > LIMIT:
            msgs.append(cur)
            cur = "market-lab (ต่อ)"
        cur += piece
    msgs.append(cur)
    ok = True
    for m in msgs:
        ok = real_send(m) and ok
    return ok


def main():
    global START
    START = daily.utcnow().isoformat(timespec="seconds")
    daily.send_telegram = patched
    daily.main()


if __name__ == "__main__":
    main()
