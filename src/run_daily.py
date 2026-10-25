import math
import sys

sys.path.insert(0, "src")
import daily

SHOW = [1, 3, 5]
NAMES = {1: "พรุ่งนี้", 3: "3 วัน", 5: "5 วัน"}
LIMIT = 3300
RED = "🔴"
GREEN = "🟢"
real_send = daily.send_telegram
START = ""


def price(x):
    return "{:,.0f}".format(x) if x >= 1000 else "{:,.2f}".format(x)


def dot(x):
    return GREEN if x > 0 else (RED if x < 0 else "⚪")


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
        b = ["%s %s %s (วันล่าสุด %+.1f%%)" % (
            dot(mv), daily.short(tk), price(last), mv * 100)]
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
    tail = []
    miss = []
    for r in res:
        if r["h"] == "1" and r["model"] == "ewma" and r["in95"] == "0":
            v = (math.exp(float(r["r_log"])) - 1) * 100
            miss.append("%s %s %+.1f%%" % (dot(v), daily.short(r["asset"]), v))
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
