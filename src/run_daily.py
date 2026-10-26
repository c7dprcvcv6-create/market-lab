import csv
import math
import pathlib
import sys

sys.path.insert(0, "src")
import daily

SHOW = [1, 3, 5]
NAMES = {1: "พรุ่งนี้", 3: "3 วัน", 5: "5 วัน"}
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


def dot(x):
    return GREEN if x > 0.0005 else (RED if x < -0.0005 else "⚪")


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
                vals = [int(x) for x in r[5:10]]
            except (ValueError, IndexError):
                continue
            x = acc.setdefault(side, [0, 0, 0, 0, 0])
            for i, v in enumerate(vals):
                x[i] += v
    out = {}
    for side, x in acc.items():
        n, tch, half, wrong, neither = x
        if n and tch:
            out[side] = (tch / n, half / tch, wrong / tch, neither / tch)
    return out


def scenario_blocks(log):
    odds = odds5()
    if not odds:
        return []
    head = ["สถานการณ์ 5 วัน (ยังไม่พิสูจน์ ใช้เรียนรู้เท่านั้น)",
            "แตะ = ราคาสูงสุดหรือต่ำสุดของวันถึงระดับนั้น",
            "ครึ่งทาง = กึ่งกลางระหว่างระดับที่แตะกับราคาปัจจุบัน",
            "ผิดทาง = ไปต่อถึงขอบช่วง 95% ก่อนถึงครึ่งทาง",
            "โอกาสมาจากสถิติ 14 ตัวรวมกัน ตั้งแต่ปี 1999 ถึง ต.ค. 2023",
            "ตรวจแล้วว่านิ่ง แต่ยังไม่พบว่าทายทิศได้ และกำไรเล็กที่เกิดบ่อย",
            "หักล้างกับขาดทุนใหญ่ที่เกิดน้อย ไม่ใช่คำแนะนำซื้อขาย"]
    blocks = [head]
    for tk in daily.TICKERS:
        r = next((x for x in reversed(log) if x["asset"] == tk
                  and x["model"] == "ewma" and x["h"] == "5"), None)
        if not r:
            continue
        last = float(r["last_close"])
        b68, b95 = float(r["b68"]), float(r["b95"])
        b = ["%s %s" % (daily.short(tk), price(last))]
        for side in (-1, 1):
            o = odds.get(side)
            if not o:
                continue
            word = "ลงแตะ" if side < 0 else "ขึ้นแตะ"
            back = "เด้งถึง" if side < 0 else "ย่อถึง"
            b.append("  %s %s (โอกาส %.0f%%)" % (
                word, price(last * math.exp(side * b68)), o[0] * 100))
            b.append("    %s %s ก่อน: %.0f%% | ผิดทางถึง %s: %.0f%% | ไม่ถึงทั้งสอง: %.0f%%" % (
                back, price(last * math.exp(side * b68 / 2)), o[1] * 100,
                price(last * math.exp(side * b95)), o[2] * 100, o[3] * 100))
        blocks.append(b)
    return blocks if len(blocks) > 1 else []


def last_move(tk):
    try:
        ds, cs = daily.make_series(daily.read_prices(tk))
        return cs[-1] / cs[-2] - 1.0
    except Exception:
        return 0.0


def build(text):
    preview = "test message" in text
    rows = daily.read_csv(daily.LOG)
    if preview:
        log, res = rows, []
        title = "market-lab ตัวอย่างข้อความ (ล่าสุดที่บันทึกไว้)"
    else:
        log = [r for r in rows if r["created_utc"] >= START]
        res = [r for r in daily.read_csv(daily.RES)
               if r["resolved_utc"] >= START]
        title = "market-lab " + START[:10]
    head = [title,
            "ช่วงที่ราคาน่าจะอยู่ (ไม่ใช่การทายขึ้นลง)",
            "% = โอกาสที่ราคาจะอยู่ในช่วงนั้น",
            RED + " = ปลายล่างของช่วง   " + GREEN + " = ปลายบนของช่วง"]
    blocks = []
    for tk in daily.TICKERS:
        hrows = {int(r["h"]): r for r in log
                 if r["asset"] == tk and r["model"] == "ewma"}
        if not hrows:
            continue
        last = float(next(iter(hrows.values()))["last_close"])
        mv = last_move(tk)
        asof = next(iter(hrows.values()))["as_of"]
        b = ["%s %s %s ปิด %s (วันล่าสุด %+.1f%%)" % (
            dot(mv), daily.short(tk), price(last), thai_date(asof), mv * 100)]
        for h in SHOW:
            r = hrows.get(h)
            if not r:
                continue
            for p, key in (("68", "b68"), ("95", "b95")):
                x = float(r[key])
                lead = NAMES[h] if p == "68" else " " * 8
                b.append("%s %s%%: %s %s - %s %s" % (
                    lead, p, RED, price(last * math.exp(-x)),
                    GREEN, price(last * math.exp(x))))
        blocks.append(b)
    blocks.extend(scenario_blocks(log))
    tail = []
    miss = []
    for r in res:
        if r["h"] == "1" and r["model"] == "ewma" and r["in95"] == "0":
            v = (math.exp(float(r["r_log"])) - 1) * 100
            miss.append("%s %s %+.1f%%" % (
                dot(v / 100), daily.short(r["asset"]), v))
    if miss:
        tail.append("ขยับแรงกว่าที่คาดมาก (เกิดได้แค่ราว 1 ใน 20 ครั้ง): "
                    + ", ".join(miss))
        tail.append("")
    allres = [r for r in daily.read_csv(daily.RES) if r["model"] == "ewma"]
    tail.append("ผลตรวจจริงตั้งแต่เริ่มระบบ (ช่วง 68% ควรถูกราว 68% ของครั้ง):")
    for h in SHOW:
        rs = [r for r in allres if r["h"] == str(h)]
        if len(rs) < 30:
            tail.append("  %s: สะสมผลอยู่ (ตรวจแล้ว %d ครั้ง)" % (NAMES[h], len(rs)))
        else:
            ok = sum(int(r["in68"]) for r in rs)
            tail.append("  %s: ถูก %.0f%% (ตรวจแล้ว %d ครั้ง)" % (
                NAMES[h], ok / len(rs) * 100, len(rs)))
    tail.append("ทดสอบย้อนหลัง 3 ปี: ช่วง 68% ถูกจริงราว 67% ช่วง 95% ถูกจริงราว 95%")
    for ln in text.split("\n"):
        if ln.startswith("ข้อควรดู:"):
            tail.append("")
            tail.append(ln)
    return head, blocks, tail


def patched(text):
    if "ดึงราคาไม่ได้" in text:
        return real_send(text)
    head, blocks, tail = build(text)
    msgs = []
    cur = "\n".join(head)
    for b in blocks + [tail]:
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
