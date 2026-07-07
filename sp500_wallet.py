#!/usr/bin/env python3
"""
Daily S&P 500 paper-wallet manager.

Once a day this script:
  1. Fetches the latest S&P 500 (^GSPC / ^SPX) closing level from the internet.
  2. Updates a *simulated* wallet that started with 5,000 shekel (ILS).
  3. Follows a simple, transparent Dollar-Cost-Averaging (DCA) rule:
     invest a fixed slice of the cash into the index each new trading day
     until the cash runs out, then just track the position's value.
  4. Rewrites `wallet.json` (machine state) and `WALLET.md` (human report,
     the "how to manage it today" note) and pushes them to GitHub.

IMPORTANT — this is a PAPER wallet:
  * No real money is ever moved. No broker is contacted.
  * This is an educational tracker, NOT financial advice.

Dependency-free: uses only the Python standard library, so it runs anywhere
Python 3 exists — no `pip install` needed.

Environment variables (all optional):
  WALLET_DCA_ILS     how many ILS to invest per new trading day   (default 250)
  WALLET_START_ILS   starting capital, only used to seed a new wallet (default 5000)
  WALLET_NO_PUSH     set to "1" to skip git commit/push (local dry run)
"""

import json
import os
import sys
import datetime
import subprocess
import pathlib
import urllib.request
import urllib.error

HERE = pathlib.Path(__file__).resolve().parent
STATE = HERE / "wallet.json"
REPORT = HERE / "WALLET.md"

CURRENCY = "ILS"
CURRENCY_SIGN = "₪"  # ₪
START_ILS = float(os.environ.get("WALLET_START_ILS", "5000"))
DCA_ILS = float(os.environ.get("WALLET_DCA_ILS", "250"))

DISCLAIMER = (
    "This is a **simulation only** — no real money is moved and no broker is "
    "contacted. It is an educational tracker, **not financial advice**. Markets "
    "can go down as well as up; never invest money you cannot afford to lose."
)


def log(msg: str) -> None:
    print(f"[wallet] {msg}")


def fail(msg: str) -> None:
    print(f"[wallet] ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def today_iso() -> str:
    return datetime.date.today().isoformat()


def iso_now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------------------- #
# Fetching the S&P 500 level (two independent free sources, no API key).
# --------------------------------------------------------------------------- #

def _http_get(url: str, timeout: int = 30) -> str:
    req = urllib.request.Request(
        url,
        headers={
            # Yahoo rejects requests without a browser-like User-Agent.
            "User-Agent": "Mozilla/5.0 (compatible; sp500-wallet/1.0)",
            "Accept": "*/*",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def fetch_stooq():
    """Return (trade_date 'YYYY-MM-DD', close float) from Stooq, or None."""
    url = "https://stooq.com/q/l/?s=^spx&f=sd2t2ohlcv&h&e=csv"
    try:
        text = _http_get(url)
    except (urllib.error.URLError, OSError) as e:
        log(f"stooq fetch failed: {e}")
        return None
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if len(lines) < 2:
        log("stooq: unexpected response")
        return None
    header = [h.strip().lower() for h in lines[0].split(",")]
    row = lines[1].split(",")
    if len(row) != len(header):
        log("stooq: header/row mismatch")
        return None
    rec = dict(zip(header, [c.strip() for c in row]))
    date = rec.get("date", "")
    close = rec.get("close", "")
    if not date or date.upper() == "N/D" or not close or close.upper() == "N/D":
        log("stooq: no data yet (N/D)")
        return None
    try:
        return date, float(close)
    except ValueError:
        log(f"stooq: cannot parse close {close!r}")
        return None


def fetch_yahoo():
    """Return (trade_date 'YYYY-MM-DD', close float) from Yahoo Finance, or None."""
    url = ("https://query1.finance.yahoo.com/v8/finance/chart/"
           "%5EGSPC?interval=1d&range=7d")
    try:
        payload = json.loads(_http_get(url))
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as e:
        log(f"yahoo fetch failed: {e}")
        return None
    try:
        result = payload["chart"]["result"][0]
        stamps = result["timestamp"]
        closes = result["indicators"]["quote"][0]["close"]
    except (KeyError, IndexError, TypeError):
        log("yahoo: unexpected response shape")
        return None
    # Walk backwards to the most recent non-null close.
    for ts, close in zip(reversed(stamps), reversed(closes)):
        if close is None:
            continue
        date = datetime.datetime.fromtimestamp(
            ts, datetime.timezone.utc).date().isoformat()
        return date, float(close)
    log("yahoo: no non-null close found")
    return None


def fetch_index():
    """Try each source in turn. Returns (source, date, close) or None."""
    for name, fn in (("stooq", fetch_stooq), ("yahoo", fetch_yahoo)):
        got = fn()
        if got:
            log(f"got S&P 500 from {name}: {got[1]} on {got[0]}")
            return name, got[0], got[1]
    return None


# --------------------------------------------------------------------------- #
# Wallet state.
# --------------------------------------------------------------------------- #

def new_wallet() -> dict:
    return {
        "currency": CURRENCY,
        "startingCapital": START_ILS,
        "strategy": "dca",
        "dcaPerTradingDay": DCA_ILS,
        "createdAt": iso_now(),
        "cash": START_ILS,          # ILS not yet invested
        "invested": 0.0,            # ILS cost basis deployed into the index
        "units": 0.0,              # simulated index units held (ILS / index level)
        "lastTradeDate": None,      # the S&P trade date we last acted on
        "lastIndex": None,          # most recent index level we have seen
        "lastRunDate": None,
        "history": [],             # one entry per new trading day
    }


def load_wallet() -> dict:
    if STATE.exists():
        try:
            return json.loads(STATE.read_text())
        except json.JSONDecodeError:
            fail(f"{STATE} is not valid JSON.")
    log("no wallet.json yet — seeding a fresh 5,000 ILS wallet.")
    return new_wallet()


def moving_average(history, n: int):
    closes = [h["index"] for h in history[-n:]]
    if not closes:
        return None
    return sum(closes) / len(closes)


def build_note(wallet: dict, is_new_day: bool, action_ils: float,
               change_pct, ma20) -> str:
    """A plain-language, descriptive (not prescriptive) market note."""
    parts = []
    if change_pct is not None:
        direction = "up" if change_pct >= 0 else "down"
        parts.append(f"The index closed {direction} {abs(change_pct):.2f}% versus the previous close.")
    if ma20 is not None and wallet["lastIndex"] is not None:
        if wallet["lastIndex"] >= ma20:
            parts.append(
                f"It is above its recent {len(wallet['history'])}-day average "
                f"({ma20:,.0f}), i.e. a recent up-trend.")
        else:
            parts.append(
                f"It is below its recent {len(wallet['history'])}-day average "
                f"({ma20:,.0f}), i.e. a recent down-trend.")
    if is_new_day and action_ils > 0:
        parts.append(
            f"Plan: invested another {CURRENCY_SIGN}{action_ils:,.0f} today "
            f"(steady DCA). {CURRENCY_SIGN}{wallet['cash']:,.0f} cash left to deploy.")
    elif is_new_day and action_ils == 0:
        parts.append("All starting cash is now invested — from here we simply track the position.")
    else:
        parts.append("US market was closed today (weekend/holiday) — no new close, position unchanged.")
    return " ".join(parts)


def update(wallet: dict, source: str, trade_date: str, index_level: float) -> dict:
    run_date = today_iso()
    prev_index = wallet.get("lastIndex")
    is_new_day = trade_date != wallet.get("lastTradeDate")

    change_pct = None
    if prev_index:
        change_pct = (index_level - prev_index) / prev_index * 100.0

    action_ils = 0.0
    if is_new_day:
        # DCA: deploy a slice of remaining cash into the index.
        invest = min(DCA_ILS, wallet["cash"])
        if invest > 0:
            wallet["units"] += invest / index_level
            wallet["cash"] -= invest
            wallet["invested"] += invest
            action_ils = invest
        wallet["lastTradeDate"] = trade_date

    wallet["lastIndex"] = index_level
    wallet["lastRunDate"] = run_date

    position_value = wallet["units"] * index_level
    total_value = wallet["cash"] + position_value

    if is_new_day:
        wallet["history"].append({
            "date": trade_date,
            "index": index_level,
            "source": source,
            "investedToday": action_ils,
            "cash": round(wallet["cash"], 2),
            "units": round(wallet["units"], 6),
            "positionValue": round(position_value, 2),
            "totalValue": round(total_value, 2),
        })

    ma20 = moving_average(wallet["history"], 20)
    wallet["_note"] = build_note(wallet, is_new_day, action_ils, change_pct, ma20)
    wallet["_changePct"] = change_pct
    wallet["_source"] = source
    wallet["_tradeDate"] = trade_date
    return wallet


# --------------------------------------------------------------------------- #
# Rendering the human report.
# --------------------------------------------------------------------------- #

def render_report(wallet: dict) -> str:
    index_level = wallet["lastIndex"]
    position_value = wallet["units"] * index_level if index_level else 0.0
    total_value = wallet["cash"] + position_value
    pnl = total_value - wallet["startingCapital"]
    pnl_pct = (pnl / wallet["startingCapital"] * 100.0) if wallet["startingCapital"] else 0.0
    sign = CURRENCY_SIGN
    change_pct = wallet.get("_changePct")
    change_str = "—" if change_pct is None else f"{change_pct:+.2f}%"
    pnl_emoji = "🟢" if pnl >= 0 else "🔴"

    rows = [
        f"# 📈 My S&P 500 Wallet",
        "",
        f"_Last updated **{wallet['lastRunDate']}** (runs daily ~03:00 Israel time)._",
        "",
        f"## Today's market",
        "",
        f"| S&P 500 close | Trade date | Daily change | Source |",
        f"|---|---|---|---|",
        f"| **{index_level:,.2f}** | {wallet.get('_tradeDate','—')} | {change_str} | {wallet.get('_source','—')} |",
        "",
        f"## My wallet",
        "",
        f"| | Amount |",
        f"|---|---|",
        f"| Starting capital | {sign}{wallet['startingCapital']:,.2f} |",
        f"| 💵 Cash (not yet invested) | {sign}{wallet['cash']:,.2f} |",
        f"| 📊 Invested so far (cost) | {sign}{wallet['invested']:,.2f} |",
        f"| 📦 Position value now | {sign}{position_value:,.2f} |",
        f"| **💰 Total wallet value** | **{sign}{total_value:,.2f}** |",
        f"| {pnl_emoji} Profit / loss | **{sign}{pnl:,.2f} ({pnl_pct:+.2f}%)** |",
        "",
        f"## 📝 How to manage it today",
        "",
        f"> {wallet.get('_note','')}",
        "",
        f"## ℹ️ How this works",
        "",
        f"- Strategy: **DCA** — invest {sign}{wallet['dcaPerTradingDay']:,.0f} of the "
        f"cash into the S&P 500 each new trading day until the {sign}{wallet['startingCapital']:,.0f} "
        f"is fully deployed, then hold and track.",
        f"- \"Units\" are simulated: investing X {CURRENCY} at index level L buys X/L units, "
        f"so the position tracks the S&P 500's percentage moves. (FX between ILS and USD is "
        f"ignored for simplicity.)",
        "",
        f"---",
        "",
        f"> ⚠️ **Disclaimer.** {DISCLAIMER}",
        "",
    ]
    return "\n".join(rows) + "\n"


# --------------------------------------------------------------------------- #

def save(wallet: dict) -> None:
    # Strip the transient underscore-prefixed fields before persisting state.
    clean = {k: v for k, v in wallet.items() if not k.startswith("_")}
    STATE.write_text(json.dumps(clean, indent=2) + "\n")
    REPORT.write_text(render_report(wallet))


def git_push() -> None:
    if os.environ.get("WALLET_NO_PUSH") == "1":
        log("WALLET_NO_PUSH=1 — skipping git commit/push (dry run).")
        return
    today = today_iso()
    run = lambda *a: subprocess.run(["git", "-C", str(HERE), *a], check=True)
    # Make sure a commit identity exists (needed on fresh CI runners).
    subprocess.run(["git", "-C", str(HERE), "config", "user.email",
                    "sp500-wallet@users.noreply.github.com"], check=False)
    subprocess.run(["git", "-C", str(HERE), "config", "user.name",
                    "sp500-wallet bot"], check=False)
    run("add", "wallet.json", "WALLET.md")
    # Nothing changed? Don't fail the job.
    if subprocess.run(["git", "-C", str(HERE), "diff", "--cached", "--quiet"]).returncode == 0:
        log("no changes to commit.")
        return
    run("commit", "-m", f"S&P 500 wallet update for {today}")
    run("push")


def main() -> None:
    got = fetch_index()
    if not got:
        fail("could not fetch the S&P 500 from any source. Try again later.")
    source, trade_date, index_level = got

    wallet = load_wallet()
    wallet = update(wallet, source, trade_date, index_level)
    save(wallet)

    total = wallet["cash"] + wallet["units"] * index_level
    log(f"S&P 500 {index_level:,.2f} ({trade_date}) — wallet value "
        f"{CURRENCY_SIGN}{total:,.2f}. {wallet['_note']}")
    git_push()
    log("done.")


if __name__ == "__main__":
    main()
