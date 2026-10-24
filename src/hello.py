import datetime
import pathlib

p = pathlib.Path("data")
p.mkdir(exist_ok=True)
now = datetime.datetime.now(datetime.timezone.utc).isoformat()
(p / "heartbeat.txt").write_text(now + "\n")
print("ok", now)
