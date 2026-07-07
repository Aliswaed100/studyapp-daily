# studyapp-daily

The cloud "database" for the **studyapp** Mac app. It's a single JSON file —
`daily.json` — in the exact format the app imports/exports. A scheduled Claude
routine adds one new question to it every day; the Mac app downloads it and
merges the new question into its **Daily Questions** deck.

```
  generate_daily_question.py   ──►   daily.json (this repo)   ──►   studyapp on your Mac
   (runs daily, calls Claude)        (committed & pushed)          (Settings ▸ Cloud ▸ Sync)
```

## The feed URL (paste this into the app)

```
https://raw.githubusercontent.com/Aliswaed100/studyapp-daily/main/daily.json
```

In the app: **⌘, → Cloud tab → Feed URL** → paste → **Sync Now**.
Leave "Sync automatically on launch" on so new questions appear each time you open the app.

## Run the generator by hand

```bash
export ANTHROPIC_API_KEY="sk-ant-..."        # required
export STUDYAPP_TOPIC="Swift programming"     # optional (default shown)
python3 generate_daily_question.py
```

Dry run (generate + write locally, don't commit/push):

```bash
STUDYAPP_NO_PUSH=1 ANTHROPIC_API_KEY="sk-ant-..." python3 generate_daily_question.py
```

## Schedule it daily

### Option A — macOS `launchd` (runs on your Mac)

Create `~/Library/LaunchAgents/com.studyapp.daily.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>com.studyapp.daily</string>
    <key>ProgramArguments</key>
    <array>
        <string>/usr/bin/python3</string>
        <string>/Users/aliswaed/Documents/important/studyapp-daily/generate_daily_question.py</string>
    </array>
    <key>EnvironmentVariables</key>
    <dict>
        <key>ANTHROPIC_API_KEY</key><string>sk-ant-REPLACE_ME</string>
        <key>STUDYAPP_TOPIC</key><string>Swift programming</string>
    </dict>
    <key>StartCalendarInterval</key>
    <dict><key>Hour</key><integer>7</integer><key>Minute</key><integer>0</integer></dict>
    <key>StandardErrorPath</key><string>/tmp/studyapp-daily.err.log</string>
    <key>StandardOutPath</key><string>/tmp/studyapp-daily.out.log</string>
</dict>
</plist>
```

Then:

```bash
launchctl load ~/Library/LaunchAgents/com.studyapp.daily.plist
```

It now runs at 07:00 every day. Logs land in `/tmp/studyapp-daily.*.log`.

### Option B — GitHub Actions (runs in the cloud, Mac can be off)

Add `.github/workflows/daily.yml` and store your key as the repo secret
`ANTHROPIC_API_KEY` (Settings ▸ Secrets and variables ▸ Actions):

```yaml
name: Daily question
on:
  schedule:
    - cron: "0 7 * * *"   # 07:00 UTC daily
  workflow_dispatch: {}
permissions:
  contents: write
jobs:
  generate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: python3 generate_daily_question.py
        env:
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
          STUDYAPP_TOPIC: "Swift programming"
```

GitHub Actions is the most reliable — it doesn't need your Mac to be awake.

## Notes

- The repo is **public** so the app can fetch `daily.json` without a token.
  Daily study questions aren't sensitive; don't commit anything private here.
- The deck `id` in `daily.json` is fixed, so every sync targets the same
  "Daily Questions" deck instead of creating duplicates. Questions are matched
  by their `id`, so re-syncing never double-adds.

---

# 📈 S&P 500 daily wallet (`sp500_wallet.py`)

A second, independent daily routine that tracks a **simulated** 5,000 ₪ wallet
against the S&P 500.

> ⚠️ **Paper money only.** No real money is moved and no broker is contacted.
> This is an educational tracker, **not financial advice.**

The wallet keeps **two balances** in `wallet.json`:

| Balance | Meaning | Starts at |
|---|---|---|
| 💵 **Cash** | ILS not yet invested | 5,000 ₪ |
| 📊 **S&P 500 Value** | market value of the index holding | 0 ₪ |

Every run it:

1. Fetches the latest **S&P 500** close from the internet (Stooq, falling back
   to Yahoo Finance — both free, no API key).
2. **Re-prices** the *S&P 500 Value* balance at today's level (so it moves with
   the market).
3. Applies a simple **buy-the-dip** rule: when the index closes **lower** than
   the day before, it moves `WALLET_BUY_ILS` (default **250 ₪**) from *Cash*
   into *S&P 500 Value*. On flat/up days it holds; it stops buying once Cash
   runs out. (`WALLET_DROP_PCT`, default `0`, lets you require a minimum daily
   drop before buying — e.g. `1` = only buy on drops of ≥1%.)
4. Rewrites two files and pushes them:
   - **[`WALLET.md`](WALLET.md)** — the report you read: today's close, both
     balances, profit/loss, and a note on exactly what it did today.
   - `wallet.json` — the machine state (both balances, units, full daily history).

### Run it by hand

```bash
python3 sp500_wallet.py                     # fetch, update, commit & push
WALLET_NO_PUSH=1 python3 sp500_wallet.py    # dry run: update files, don't push
WALLET_BUY_ILS=100 WALLET_DROP_PCT=1 python3 sp500_wallet.py   # tweak the rule
```

### How it runs daily

Two pieces work together, because the market data can only be fetched from a
network with open internet access:

1. **Engine — GitHub Actions** runs
   **[`.github/workflows/sp500-wallet.yml`](.github/workflows/sp500-wallet.yml)**
   on a schedule (`22:00 UTC`, after the US close). GitHub's runners have open
   internet, so they can reach the price sources. The job runs `sp500_wallet.py`,
   which commits & pushes the updated `wallet.json` + `WALLET.md`.
2. **Notifier — a Claude Routine** runs a couple of hours later
   (`00:00 UTC` ≈ **03:00 Israel time**), reads the freshly-updated `WALLET.md`,
   and sends a phone notification with the day's numbers.

**See it right now:** open **Actions ▸ *S&P 500 daily wallet* ▸ Run workflow**
to fetch today's real numbers immediately instead of waiting for the schedule.

> Why two pieces? Claude Code's cloud environment sits behind an egress policy
> that blocks the finance data hosts, so the fetch itself has to happen on
> GitHub's runners. The Routine only *reads* the result (reaching GitHub is
> allowed) and notifies you.
>
> Scheduled workflows are auto-disabled after ~60 days with no repo activity;
> the daily commits from this and the questions routine keep it alive.
