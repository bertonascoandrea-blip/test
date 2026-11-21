#!/usr/bin/env python3
"""Daily day-trade ideas for a Trade Republic account, built on the trader-* skills.

Pipeline, all with the skills' own scripts:
  scanner (opening-range breakouts on daytrade/universe.txt)
  -> for each breakout still holding above the range: a plan from the CURRENT price
     (stop at the range midpoint, target 1R or 2R, whichever is better)
  -> Time Machine (10,000 replays + the same rule on the last ~59 sessions)
  -> budget (whole shares, capital and max loss converted EUR -> USD)
  -> risk check (PASS / BLOCK)
  -> keep only ideas whose expected result is positive AFTER Trade Republic's
     1 EUR + 1 EUR fees, in the replays AND in the real history; rank, top N.

No idea is invented: if nothing passes, the report says so. Nothing is ever placed.

  python3 daytrade/daily.py                       # now
  python3 daytrade/daily.py --now "2026-11-20 10:20"
  python3 daytrade/daily.py --max-loss-eur 30 --capital-eur 1500
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
SK = os.path.join(ROOT, ".claude", "skills")
NY = ZoneInfo("America/New_York")
ROME = ZoneInfo("Europe/Rome")
UA = "day-trader-skill (personal use)"


def py(script, *args):
    """Run a skill script, return (returncode, stdout, stderr)."""
    p = subprocess.run([sys.executable, "-I", os.path.join(SK, script)] + list(args),
                       capture_output=True, text=True, cwd=ROOT, timeout=600)
    return p.returncode, p.stdout, p.stderr


def get_json(url):
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.read().decode("utf-8", "replace")
        except Exception:
            if attempt == 2:
                raise
            time.sleep(3 * (attempt + 1))


def eurusd():
    t = get_json("https://query2.finance.yahoo.com/v8/finance/chart/EURUSD=X?range=1d&interval=5m")
    return float(json.loads(t)["chart"]["result"][0]["meta"]["regularMarketPrice"])


def parse_now(s):
    if not s:
        return dt.datetime.now(NY).replace(second=0, microsecond=0)
    return dt.datetime.strptime(s, "%Y-%m-%d %H:%M").replace(tzinfo=NY)


def last_completed_bar(chart_text, day, now):
    """(index, close, close_time) of the last 5-min bar of `day` that closed by `now`."""
    r = json.loads(chart_text)["chart"]["result"][0]
    q = r["indicators"]["quote"][0]
    best = None
    for i, t in enumerate(r.get("timestamp") or []):
        start = dt.datetime.fromtimestamp(t, NY)
        if start.date() != day or q["close"][i] is None:
            continue
        idx = (start.hour * 60 + start.minute - 570) // 5
        end = start + dt.timedelta(minutes=5)
        if idx < 0 or end > now:
            continue
        best = (idx, float(q["close"][i]), end)
    return best


def ampm(t):
    return t.strftime("%I:%M %p").lstrip("0")


def rome(t):
    return t.astimezone(ROME).strftime("%H:%M")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--capital-eur", type=float, default=1000)
    ap.add_argument("--max-loss-eur", type=float, default=50, help="max loss per trade, fees included")
    ap.add_argument("--fee-eur", type=float, default=1.0, help="Trade Republic fee per order")
    ap.add_argument("--top", type=int, default=3)
    ap.add_argument("--since", help="only breakouts that triggered at/after HH:MM New York")
    ap.add_argument("--now", help='pretend it is this New York time, "YYYY-MM-DD HH:MM"')
    ap.add_argument("--eurusd", type=float)
    ap.add_argument("--universe", default=os.path.join(ROOT, "daytrade", "universe.txt"))
    ap.add_argument("--min-history", type=int, default=10, help="past triggers needed to trust the history")
    ap.add_argument("--out", default=os.path.join(ROOT, "trader", "daily"))
    a = ap.parse_args()

    now = parse_now(a.now)
    nows = now.strftime("%Y-%m-%d %H:%M")
    run = os.path.join(a.out, now.strftime("%Y-%m-%d_%H%M"))
    cache = os.path.join(run, "charts")
    os.makedirs(cache, exist_ok=True)

    fx = a.eurusd or eurusd()
    fees = 2 * a.fee_eur
    cfg = {
        "budget": round(a.capital_eur * fx, 2), "cash_buffer_pct": 2, "max_position_pct": 100,
        "max_order_usd": round(a.capital_eur * fx, 2), "min_order_usd": 5,
        "max_loss_per_trade_usd": round((a.max_loss_eur - fees) * fx, 2),
        "max_daily_loss_usd": round(3 * a.max_loss_eur * fx, 2),
        "max_trades_per_day": 3, "max_orders_per_day": 10, "min_reward_risk": 1.0,
        "require_stop": True, "no_entries_before": "09:45", "no_entries_after": "15:30",
        "flat_by": "15:55", "fractional": False, "broker": "trade-republic (manual)", "paper": True,
        "blocked": [], "allow_options": False, "allow_margin": False, "allow_short": False,
        "pdt_guard": False,
    }
    cfgp = os.path.join(run, "config.json")
    json.dump(cfg, open(cfgp, "w"), indent=1)

    lines = []
    out = lines.append
    out("# Idee day trading — %s, %s New York (%s Italia)" % (now.strftime("%a %d/%m/%Y"), now.strftime("%H:%M"), rome(now)))
    out("")
    out("Capitale %.0f €, perdita max %.0f € a operazione (commissioni %.0f € incluse), EUR/USD %.4f." % (
        a.capital_eur, a.max_loss_eur, fees, fx))
    out("")

    wl = os.path.join(run, "watchlist.json")
    rc, so, se = py("trader-scanner/scan.py", "orb", "--universe", a.universe, "--now", nows,
                    "--out", wl, "--config", cfgp, "--quiet")
    if rc != 0 or not os.path.exists(wl):
        msg = (se or so).strip().splitlines()[-1:] or ["scanner failed"]
        out("**Nessuna idea.** Lo scanner non ha prodotto risultati: %s" % msg[0])
        return finish(lines, run)
    hits = json.load(open(wl)).get("hits", [])
    if a.since:
        hh, mm = map(int, a.since.split(":"))
        def trig(h):
            t = dt.datetime.strptime(h["at"], "%I:%M %p")
            return (t.hour, t.minute) >= (hh, mm)
        hits = [h for h in hits if trig(h)]
    out("Scanner: %d rotture del range di apertura (15 min) tra i titoli monitorati%s." % (
        len(hits), (" dalle %s" % a.since) if a.since else ""))

    ideas, rejected = [], {"sotto il range": 0, "piano rifiutato": 0, "simulazione": 0,
                           "storia insufficiente": 0, "negativa dopo commissioni": 0, "rischio BLOCK": 0, "dati": 0}
    for h in hits:
        sym = h["symbol"]
        cp = os.path.join(cache, "%s-60d5m.json" % sym)
        try:
            if not os.path.exists(cp):
                txt = get_json("https://query2.finance.yahoo.com/v8/finance/chart/%s?range=60d&interval=5m" % sym)
                open(cp, "w").write(txt)
                time.sleep(0.4)
            txt = open(cp).read()
            lb = last_completed_bar(txt, now.date(), now)
        except Exception:
            rejected["dati"] += 1
            continue
        if not lb:
            rejected["dati"] += 1
            continue
        idx, last, tclose = lb
        if last <= h["or_high"]:
            rejected["sotto il range"] += 1
            continue
        best = None
        for tgt in ("1R", "2R"):
            pp = os.path.join(run, "%s-%s-plan.json" % (sym, tgt))
            rc, so, se = py("trader-exit/exit.py", "plan", "--symbol", sym, "--entry", "%.2f" % last,
                            "--or-high", str(h["or_high"]), "--or-low", str(h["or_low"]),
                            "--date", now.date().isoformat(), "--time", ampm(tclose), "--entry-bar", str(idx),
                            "--setup", "orb", "--stop", "or-mid", "--target", tgt, "--config", cfgp,
                            "--now", nows, "--write", pp, "--json")
            if rc != 0 or not os.path.exists(pp):
                continue
            rc, so, se = py("trader-timemachine/timemachine.py", "run", "--plan", pp, "--offline", cache,
                            "--now", nows, "--json")
            if rc != 0:
                continue
            tm = json.loads(so)
            cand = (tgt, pp, tm)
            if best is None or tm["mc"]["avg_R"] > best[2]["mc"]["avg_R"]:
                best = cand
        if best is None:
            rejected["piano rifiutato"] += 1
            continue
        tgt, pp, tm = best
        mc, hist = tm["mc"], tm["history"]
        if mc["p_target"] <= mc["p_stop"]:
            rejected["simulazione"] += 1
            continue
        if hist.get("triggered", 0) < a.min_history:
            rejected["storia insufficiente"] += 1
            continue
        op = os.path.join(run, "%s-orders.json" % sym)
        rc, so, se = py("trader-budget/size.py", "--plan", pp, "--config", cfgp, "--now", nows, "--write", op, "--json")
        if rc != 0 or not os.path.exists(op):
            rejected["rischio BLOCK"] += 1
            continue
        orders = json.load(open(op))
        olist = orders.get("orders", orders if isinstance(orders, list) else [])
        if not olist:
            rejected["rischio BLOCK"] += 1
            continue
        o = olist[0]
        qty = int(float(o.get("qty") or o.get("quantity") or 0))
        rc, so, se = py("trader-risk/check.py", "--orders", op, "--config", cfgp, "--now", nows, "--no-write", "--json")
        try:
            rk = json.loads(so)
            verdicts = rk.get("verdicts") or rk.get("results") or rk.get("orders") or []
            ok = bool(verdicts) and all(str(v.get("verdict", v.get("status", ""))).upper() == "PASS" for v in verdicts)
        except Exception:
            ok = False
        if not ok or qty <= 0:
            rejected["rischio BLOCK"] += 1
            continue
        plan = json.load(open(pp))
        risk_eur = qty * plan["risk_per_share"] / fx
        ev_mc = mc["avg_R"] * risk_eur - fees
        ev_hist = hist["avg_R"] * risk_eur - fees
        if ev_mc <= 0 or ev_hist <= 0:
            rejected["negativa dopo commissioni"] += 1
            continue
        ideas.append(dict(sym=sym, plan=plan, tm=tm, qty=qty, risk_eur=risk_eur, ev_mc=ev_mc,
                          ev_hist=ev_hist, score=min(ev_mc, ev_hist), trig=h["at"], last=last, tclose=tclose))

    ideas.sort(key=lambda d: -d["score"])
    ideas = ideas[:a.top]
    json.dump([dict(sym=d["sym"], qty=d["qty"], entry=d["plan"]["entry"], stop=d["plan"]["stop"],
                    target=d["plan"]["target"], entry_bar=d["plan"]["entry_bar"], date=d["plan"]["date"],
                    risk_eur=round(d["risk_eur"], 2), ev_mc=round(d["ev_mc"], 2), ev_hist=round(d["ev_hist"], 2),
                    eurusd=fx) for d in ideas], open(os.path.join(run, "ideas.json"), "w"), indent=1)
    out("Scartate: " + ", ".join("%d %s" % (n, k) for k, n in rejected.items() if n) + "." if any(rejected.values()) else "")
    out("")
    if not ideas:
        out("## Oggi nessuna idea passa i filtri")
        out("")
        out("Nessuna rottura ha un risultato atteso positivo dopo le commissioni sia nelle 10.000 simulazioni "
            "sia nella storia reale degli ultimi ~60 giorni. Non entrare: anche non fare niente è una decisione.")
        return finish(lines, run)

    flat = now.replace(hour=15, minute=55)
    for n, d in enumerate(ideas, 1):
        p, mc, hist = d["plan"], d["tm"]["mc"], d["tm"]["history"]
        e, s, t = p["entry"], p["stop"], p["target"]
        lim = round(e * 1.002, 2)
        out("## %d. %s — %s azioni" % (n, d["sym"], d["qty"]))
        out("")
        out("| | USD | EUR circa |")
        out("|---|---|---|")
        out("| Entrata (ultimo prezzo, %s Italia) | %.2f | %.2f |" % (rome(d["tclose"]), e, e / fx))
        out("| Limite d'acquisto massimo | %.2f | %.2f |" % (lim, lim / fx))
        out("| **Stop-loss** | %.2f | **%.2f** |" % (s, s / fx))
        out("| **Take-profit** (%s) | %.2f | **%.2f** |" % (p.get("target_label", "").split(" ")[0], t, t / fx))
        out("")
        out("- Investimento: %.0f €. Perdita allo stop: %.2f € + %.0f € commissioni = **%.2f €**. Guadagno al target: %.2f € − %.0f € = **%.2f €**." % (
            d["qty"] * e / fx, d["risk_eur"], fees, d["risk_eur"] + fees,
            d["qty"] * (t - e) / fx, fees, d["qty"] * (t - e) / fx - fees))
        out("- Time Machine: target per primo %.1f%%, stop per primo %.1f%%, aperto a fine giornata %.1f%%." % (
            100 * mc["p_target"], 100 * mc["p_stop"], 100 * mc["p_neither"]))
        out("- Storia reale (stessa regola, ultime %d sedute): scattata %d volte, %d target, %d stop, %d né l'uno né l'altro." % (
            hist["sessions"], hist["triggered"], hist["target"], hist["stop"], hist["close"]))
        out("- Risultato atteso dopo commissioni: %+.2f € (simulazioni), %+.2f € (storia). Rottura iniziale alle %s New York." % (
            d["ev_mc"], d["ev_hist"], d["trig"]))
        out("- Chiudi comunque entro le **%s** italiane." % rome(flat))
        out("")
    out("## Come farlo su Trade Republic")
    out("")
    out("1. Prendine **una alla volta**: con %.0f € il capitale basta per una posizione." % a.capital_eur)
    out("2. Compra con un **ordine limite** al prezzo massimo indicato (non inseguire se è già sopra).")
    out("3. Appena eseguito, imposta lo **stop-loss** in EUR al livello in tabella.")
    out("4. Metti un **avviso di prezzo** al take-profit e vendi quando suona (se Trade Republic non ti permette "
        "di avere stop-loss e ordine limite insieme sulle stesse azioni).")
    out("5. Alle %s italiane vendi quello che è ancora aperto." % rome(flat))
    out("")
    out("I prezzi in EUR sono convertiti al cambio attuale: controlla la quotazione in app (Lang & Schwarz) prima di "
        "inviare. Simulazione, non previsione. Non è consulenza finanziaria.")
    return finish(lines, run)


def finish(lines, run):
    text = "\n".join(l for l in lines if l is not None)
    open(os.path.join(run, "report.md"), "w").write(text + "\n")
    print(text)
    print("\n(report salvato in %s)" % os.path.relpath(os.path.join(run, "report.md"), ROOT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
