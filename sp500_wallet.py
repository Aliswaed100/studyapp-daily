#!/usr/bin/env python3
"""
Daily S&P 500 paper-wallet manager.

The wallet keeps TWO separate balances (a little "memory" in wallet.json):

    Cash            ILS sitting on the side, not invested       (starts 5,000)
    S&P 500 Value   current market value of the index holding   (starts 0)

Once a day this script:
  1. Fetches the latest S&P 500 (^GSPC / ^SPX) close from the internet.
  2. Re-prices the "S&P 500 Value" balance at today's level (mark-to-market).
  3. Applies a simple, transparent rule — BUY THE DIP: when the index closes
     lower than the day before, move a fixed amount from Cash into the S&P 500.
     On flat/up days it just holds.
  4. Saves the new state to `wallet.json` and writes a plain-language report of
     exactly what it did to `WALLET.md`, then pushes both to GitHub.

IMPORTANT — this is a PAPER wallet:
  * No real money is ever moved. No broker is contacted.
  * This is an educational tracker, NOT financial advice.

Dependency-free: uses only the Python standard library — no `pip install`.

Environment variables (all optional):
  WALLET_BUY_ILS     how many ILS to move Cash -> S&P on a down day (default 250)
  WALLET_DROP_PCT    only buy if the index fell at least this %      (default 0)
                     e.g. 1 = only buy on drops of 1% or more
  WALLET_START_ILS   starting Cash, only used to seed a new wallet   (default 5000)
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
SIGN = "₪"
START_ILS = float(os.environ.get("WALLET_START_ILS", "5000"))
BUY_ILS = float(os.environ.get("WALLET_BUY_ILS", "250"))
DROP_PCT = float(os.environ.get("WALLET_DROP_PCT", "0"))

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
# Wallet state — the two-balance "memory".
# --------------------------------------------------------------------------- #

def new_wallet() -> dict:
    return {
        "currency": CURRENCY,
        "startingCapital": START_ILS,
        "createdAt": iso_now(),
        "rule": {
            "type": "buy-the-dip",
            "buyAmountILS": BUY_ILS,
            "dropThresholdPct": DROP_PCT,
        },
        # ---- the two balances you watch ----
        "cash": START_ILS,          # ILS not invested
        "sp500Value": 0.0,          # market value of the S&P 500 holding
        # ---- supporting bookkeeping ----
        "units": 0.0,               # simulated index units (ILS / index level)
        "invested": 0.0,            # total ILS moved Cash -> S&P (cost basis)
        "totalValue": START_ILS,    # cash + sp500Value
        "profitLoss": 0.0,          # totalValue - startingCapital
        "lastTradeDate": None,      # S&P trade date we last acted on
        "lastIndex": None,          # most recent index level seen
        "lastRunDate": None,
        "history": [],              # one entry per new trading day
    }


def load_wallet() -> dict:
    if STATE.exists():
        try:
            return json.loads(STATE.read_text())
        except json.JSONDecodeError:
            fail(f"{STATE} is not valid JSON.")
    log("no wallet.json yet — seeding a fresh 5,000 ILS wallet.")
    return new_wallet()


def build_note(action: str, change_pct, bought: float, wallet: dict) -> str:
    c = f"{SIGN}{wallet['cash']:,.0f}"
    if action == "closed":
        return ("US market was closed today (weekend/holiday) — no new close. "
                "The S&P 500 balance is shown at its last price; nothing bought.")
    if action == "baseline":
        return (f"First reading recorded (S&P 500 = {wallet['lastIndex']:,.2f}). "
                f"Waiting for the first down day to make the first buy. Cash: {c}.")
    if action == "buy":
        return (f"📉 S&P 500 fell {abs(change_pct):.2f}% today → moved "
                f"**{SIGN}{bought:,.0f} from Cash into the S&P 500** (buy-the-dip). "
                f"Cash left: {c}.")
    if action == "hold-empty":
        return ("All cash has been deployed into the S&P 500 — now just holding "
                "and tracking its value.")
    # plain hold (up/flat day)
    move = "rose" if (change_pct or 0) > 0 else "was flat"
    return (f"S&P 500 {move} "
            f"{'' if change_pct is None else f'{change_pct:+.2f}% '}"
            f"today → no buy today, holding. Cash: {c}.")


def update(wallet: dict, source: str, trade_date: str, index_level: float) -> dict:
    prev_index = wallet.get("lastIndex")
    is_new_day = trade_date != wallet.get("lastTradeDate")
    change_pct = None
    if prev_index:
        change_pct = (index_level - prev_index) / prev_index * 100.0

    action = "closed"
    bought = 0.0
    if is_new_day:
        if change_pct is None:
            action = "baseline"
        elif change_pct <= -DROP_PCT and wallet["cash"] > 0:
            # Market dropped -> move a fixed amount Cash -> S&P 500.
            bought = min(BUY_ILS, wallet["cash"])
            wallet["units"] += bought / index_level
            wallet["cash"] -= bought
            wallet["invested"] += bought
            action = "buy"
        elif wallet["cash"] <= 0:
            action = "hold-empty"
        else:
            action = "hold"
        wallet["lastTradeDate"] = trade_date

    # Re-price the S&P 500 balance at today's level (every run).
    wallet["lastIndex"] = index_level
    wallet["lastRunDate"] = today_iso()
    wallet["sp500Value"] = round(wallet["units"] * index_level, 2)
    wallet["cash"] = round(wallet["cash"], 2)
    wallet["totalValue"] = round(wallet["cash"] + wallet["sp500Value"], 2)
    wallet["profitLoss"] = round(wallet["totalValue"] - wallet["startingCapital"], 2)

    if is_new_day:
        wallet["history"].append({
            "date": trade_date,
            "index": index_level,
            "changePct": None if change_pct is None else round(change_pct, 2),
            "action": action,
            "boughtILS": round(bought, 2),
            "cash": wallet["cash"],
            "sp500Value": wallet["sp500Value"],
            "totalValue": wallet["totalValue"],
            "source": source,
        })

    wallet["_note"] = build_note(action, change_pct, bought, wallet)
    wallet["_changePct"] = change_pct
    wallet["_source"] = source
    wallet["_tradeDate"] = trade_date
    return wallet


# --------------------------------------------------------------------------- #
# Human report.
# --------------------------------------------------------------------------- #

def render_report(wallet: dict) -> str:
    idx = wallet["lastIndex"]
    change_pct = wallet.get("_changePct")
    change_str = "—" if change_pct is None else f"{change_pct:+.2f}%"
    pnl = wallet["profitLoss"]
    pnl_pct = (pnl / wallet["startingCapital"] * 100.0) if wallet["startingCapital"] else 0.0
    pnl_emoji = "🟢" if pnl >= 0 else "🔴"
    rule = wallet["rule"]
    thresh = rule["dropThresholdPct"]
    drop_desc = "closes lower than the day before" if thresh == 0 else \
        f"drops by {thresh:g}% or more in a day"

    lines = [
        "# 📈 My S&P 500 Wallet",
        "",
        f"_Last updated **{wallet['lastRunDate']}** (runs daily ~03:00 Israel time)._",
        "",
        "## Today's market",
        "",
        "| S&P 500 close | Trade date | Daily change | Source |",
        "|---|---|---|---|",
        f"| **{idx:,.2f}** | {wallet.get('_tradeDate','—')} | {change_str} | {wallet.get('_source','—')} |",
        "",
        "## My two balances",
        "",
        "| Balance | Amount |",
        "|---|---|",
        f"| 💵 **Cash** | **{SIGN}{wallet['cash']:,.2f}** |",
        f"| 📊 **S&P 500 Value** | **{SIGN}{wallet['sp500Value']:,.2f}** |",
        f"| 💰 Total wallet | {SIGN}{wallet['totalValue']:,.2f} |",
        f"| {pnl_emoji} Profit / loss | {SIGN}{pnl:,.2f} ({pnl_pct:+.2f}%) |",
        "",
        f"_(Starting capital: {SIGN}{wallet['startingCapital']:,.2f}. Invested into "
        f"the S&P 500 so far: {SIGN}{wallet['invested']:,.2f}.)_",
        "",
        "## 📝 What the wallet did today",
        "",
        f"> {wallet.get('_note','')}",
        "",
        "## ℹ️ The rule",
        "",
        f"- **Buy the dip:** whenever the S&P 500 {drop_desc}, move "
        f"**{SIGN}{rule['buyAmountILS']:,.0f}** from *Cash* into *S&P 500 Value*. "
        f"On flat/up days it holds. It stops buying once Cash runs out.",
        f"- \"S&P 500 Value\" is re-priced every day from the live index, so it "
        f"moves up and down with the market. (FX between ILS and USD is ignored "
        f"for simplicity.)",
        "",
        "---",
        "",
        f"> ⚠️ **Disclaimer.** {DISCLAIMER}",
        "",
    ]
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #

def save(wallet: dict) -> None:
    clean = {k: v for k, v in wallet.items() if not k.startswith("_")}
    STATE.write_text(json.dumps(clean, indent=2) + "\n")
    REPORT.write_text(render_report(wallet))


def git_push() -> None:
    if os.environ.get("WALLET_NO_PUSH") == "1":
        log("WALLET_NO_PUSH=1 — skipping git commit/push (dry run).")
        return
    today = today_iso()
    run = lambda *a: subprocess.run(["git", "-C", str(HERE), *a], check=True)
    subprocess.run(["git", "-C", str(HERE), "config", "user.email",
                    "sp500-wallet@users.noreply.github.com"], check=False)
    subprocess.run(["git", "-C", str(HERE), "config", "user.name",
                    "sp500-wallet bot"], check=False)
    run("add", "wallet.json", "WALLET.md")
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

    log(f"S&P 500 {index_level:,.2f} ({trade_date}) | Cash {SIGN}{wallet['cash']:,.2f} "
        f"| S&P 500 Value {SIGN}{wallet['sp500Value']:,.2f} "
        f"| Total {SIGN}{wallet['totalValue']:,.2f}. {wallet['_note']}")
    git_push()
    log("done.")


if __name__ == "__main__":
    main()
