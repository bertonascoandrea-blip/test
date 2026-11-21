#!/usr/bin/env python3
"""Hourly check: at most ONE day-trade idea per day, sent to WhatsApp the moment it appears.

Runs at the checkpoints below (New York time). Each run is a fresh session with no memory,
so it replays today's earlier checkpoints on the same data (the pipeline is deterministic:
same bars, same seed) to know whether an idea was already sent. Then:
  - an earlier checkpoint had an idea  -> already sent, stay quiet
  - this checkpoint has one            -> send the single best idea now
  - last checkpoint and nothing all day -> send one "no idea today" message

  python3 daytrade/hourly.py                     # now
  python3 daytrade/hourly.py --now "2026-11-17 11:20" --dry-run
"""
import argparse
import datetime as dt
import json
import os
import subprocess
import sys
import time
import urllib.request
from zoneinfo import ZoneInfo

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NY = ZoneInfo("America/New_York")
CHECKPOINTS = ["10:20", "11:20", "12:20", "13:20", "14:20", "15:20"]


def fetch_charts(universe, folder):
    os.makedirs(folder, exist_ok=True)
    for line in open(universe):
        sym = line.split("|")[0].strip()
        if not sym:
            continue
        path = os.path.join(folder, sym + ".json")
        for attempt in range(3):
            try:
                req = urllib.request.Request(
                    "https://query2.finance.yahoo.com/v8/finance/chart/%s?range=60d&interval=5m" % sym,
                    headers={"User-Agent": "day-trader-skill (personal use)"})
                txt = urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "replace")
                json.loads(txt)
                open(path, "w").write(txt)
                break
            except Exception:
                time.sleep(3 * (attempt + 1))
        time.sleep(0.35)


def run_daily(now, charts, out, a, since=None):
    args = [sys.executable, os.path.join(ROOT, "daytrade", "daily.py"), "--now", now.strftime("%Y-%m-%d %H:%M"),
            "--offline", charts, "--out", out, "--top", "1", "--capital-eur", str(a.capital_eur),
            "--max-loss-eur", str(a.max_loss_eur)]
    if a.eurusd:
        args += ["--eurusd", str(a.eurusd)]
    if since:
        args += ["--since", since]
    p = subprocess.run(args, capture_output=True, text=True, cwd=ROOT, timeout=1800)
    run = os.path.join(out, now.strftime("%Y-%m-%d_%H%M"))
    ideas_path = os.path.join(run, "ideas.json")
    ideas = json.load(open(ideas_path)) if os.path.exists(ideas_path) else []
    return ideas, os.path.join(run, "report.md"), p.stdout + p.stderr


def whatsapp(args, dry):
    cmd = [sys.executable, os.path.join(ROOT, "daytrade", "notify_whatsapp.py")] + args + (["--dry-run"] if dry else [])
    p = subprocess.run(cmd, capture_output=True, text=True, cwd=ROOT)
    if p.returncode != 0 and not dry:
        time.sleep(30)
        p = subprocess.run(cmd, capture_output=True, text=True, cwd=ROOT)
    return p.returncode, (p.stdout + p.stderr).strip()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--now")
    ap.add_argument("--capital-eur", type=float, default=1000)
    ap.add_argument("--max-loss-eur", type=float, default=100)
    ap.add_argument("--eurusd", type=float)
    ap.add_argument("--charts", help="use these saved charts instead of downloading")
    ap.add_argument("--dry-run", action="store_true", help="print the WhatsApp text, do not send")
    a = ap.parse_args()

    now = (dt.datetime.strptime(a.now, "%Y-%m-%d %H:%M").replace(tzinfo=NY) if a.now
           else dt.datetime.now(NY).replace(second=0, microsecond=0))
    day = now.date()
    if now.weekday() >= 5:
        print("Weekend: market closed, nothing to do.")
        return 0
    cps = [dt.datetime.combine(day, dt.time(*map(int, c.split(":"))), NY) for c in CHECKPOINTS]
    past = [c for c in cps if c <= now]
    if not past:
        print("Before the first checkpoint (%s New York): nothing to do yet." % CHECKPOINTS[0])
        return 0
    current, earlier = past[-1], past[:-1]
    is_last = current == cps[-1]

    out = os.path.join(ROOT, "trader", "hourly", day.isoformat())
    charts = a.charts or os.path.join(out, "charts-%s" % now.strftime("%H%M"))
    if not a.charts:
        fetch_charts(os.path.join(ROOT, "daytrade", "universe.txt"), charts)

    for c in earlier:
        ideas, _, log = run_daily(c, charts, out, a)
        if "No session" in log or "market is closed" in log:
            print("Market closed today: nothing to do.")
            return 0
        if ideas:
            print("Already sent today at %s New York: %s. Staying quiet." % (c.strftime("%H:%M"), ideas[0]["sym"]))
            return 0

    ideas, report, log = run_daily(current, charts, out, a)
    if "No session" in log or "market is closed" in log:
        if current == cps[0]:
            rc, msg = whatsapp(["--text", "📉 Day Trader: oggi la borsa USA è chiusa, nessuna idea."], a.dry_run)
            print(msg)
        print("Market closed today.")
        return 0
    if ideas:
        rc, msg = whatsapp([report], a.dry_run)
        print("Idea of the day at %s New York: %s\n%s" % (current.strftime("%H:%M"), ideas[0]["sym"], msg))
        print(open(report).read())
        return rc
    if is_last:
        rc, msg = whatsapp(["--text", "📊 Day Trader, %s: oggi nessuna azione è stata profittevole dopo le "
                            "commissioni in nessun controllo (16:20–21:20). Nessuna operazione: anche "
                            "questo protegge il capitale." % day.strftime("%d/%m")], a.dry_run)
        print("No idea all day.\n" + msg)
        return rc
    print("No idea at %s New York; next check in an hour." % current.strftime("%H:%M"))
    print(open(report).read() if os.path.exists(report) else log[-2000:])
    return 0


if __name__ == "__main__":
    sys.exit(main())
