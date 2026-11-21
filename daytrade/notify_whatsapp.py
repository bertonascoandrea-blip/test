#!/usr/bin/env python3
"""Send a daily.py report to your own WhatsApp through CallMeBot (free, personal use).

Needs two environment variables, set in the cloud environment's settings (never in chat):
  CALLMEBOT_PHONE   your WhatsApp number with country code, e.g. +393331234567
  CALLMEBOT_APIKEY  the apikey the CallMeBot bot sent you on WhatsApp

  python3 daytrade/notify_whatsapp.py trader/daily/2026-11-23_1020/report.md
  python3 daytrade/notify_whatsapp.py --text "prova"
  python3 daytrade/notify_whatsapp.py --dry-run REPORT     # print, do not send
"""
import argparse
import os
import re
import sys
import time
import urllib.parse
import urllib.request

MAX = 1200  # characters per WhatsApp message; longer reports are split


def to_whatsapp(md):
    """Markdown report -> WhatsApp text (*bold*, no tables)."""
    out = []
    for line in md.splitlines():
        s = line.rstrip()
        if re.match(r"^\|?\s*-{3,}", s):
            continue  # table separator
        if s.startswith("|"):
            cells = [c.strip() for c in s.strip("|").split("|")]
            if not cells[0]:
                continue  # table header with empty first cell
            s = "• " + cells[0] + ": " + " — ".join(c for c in cells[1:] if c)
        s = re.sub(r"^#+\s*", "", s) if s.startswith("#") else s
        if line.startswith("#"):
            s = "*" + s.strip("*") + "*"
        s = s.replace("**", "*").replace("`", "")
        s = s.replace("(report salvato", "").strip()
        out.append(s)
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()
    return text


def chunks(text):
    parts, cur = [], ""
    for block in text.split("\n\n"):
        if cur and len(cur) + len(block) + 2 > MAX:
            parts.append(cur)
            cur = block
        else:
            cur = (cur + "\n\n" + block) if cur else block
    if cur:
        parts.append(cur)
    out = []
    for p in parts:  # a single block longer than MAX
        while len(p) > MAX:
            cut = p.rfind("\n", 0, MAX)
            cut = cut if cut > 0 else MAX
            out.append(p[:cut])
            p = p[cut:].lstrip("\n")
        out.append(p)
    return out


def send(phone, apikey, text):
    url = "https://api.callmebot.com/whatsapp.php?" + urllib.parse.urlencode(
        {"phone": phone, "text": text, "apikey": apikey})
    req = urllib.request.Request(url, headers={"User-Agent": "day-trader-skill"})
    with urllib.request.urlopen(req, timeout=60) as r:
        body = r.read().decode("utf-8", "replace")
    if r.status != 200 or "ERROR" in body.upper() and "QUEUED" not in body.upper():
        raise RuntimeError("CallMeBot: HTTP %s %s" % (r.status, re.sub(r"<[^>]+>", " ", body)[:200]))
    return body


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("report", nargs="?")
    ap.add_argument("--text")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    if a.text:
        text = a.text
    elif a.report:
        text = to_whatsapp(open(a.report, encoding="utf-8").read())
    else:
        ap.error("give a report path or --text")
    parts = chunks(text)
    if len(parts) > 1:
        parts = ["(%d/%d)\n%s" % (i, len(parts), p) for i, p in enumerate(parts, 1)]
    if a.dry_run:
        print("\n\n=====\n\n".join(parts))
        return 0
    phone, key = os.environ.get("CALLMEBOT_PHONE", "").strip(), os.environ.get("CALLMEBOT_APIKEY", "").strip()
    if not phone or not key:
        print("notify_whatsapp: CALLMEBOT_PHONE / CALLMEBOT_APIKEY not set in the environment", file=sys.stderr)
        return 2
    for i, p in enumerate(parts):
        if i:
            time.sleep(4)  # be gentle with the free service
        send(phone, key, p)
    print("WhatsApp: sent %d message(s)" % len(parts))
    return 0


if __name__ == "__main__":
    sys.exit(main())
