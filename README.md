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
invested in the S&P 500.

> ⚠️ **Paper money only.** No real money is moved and no broker is contacted.
> This is an educational tracker, **not financial advice.**

Every run it:

1. Fetches the latest **S&P 500** close from the internet (Stooq, falling back
   to Yahoo Finance — both free, no API key).
2. Follows a simple **DCA** rule: invest `WALLET_DCA_ILS` (default **250 ₪**) of
   the cash into the index each new trading day until the 5,000 ₪ is fully
   deployed, then just hold and track.
3. Rewrites two files and pushes them:
   - **[`WALLET.md`](WALLET.md)** — the human report you read: today's close,
     your wallet value, profit/loss, and a plain-language "how to manage it
     today" note.
   - `wallet.json` — the machine state (cash, units, full daily history).

### Run it by hand

```bash
python3 sp500_wallet.py                 # fetch, update, commit & push
WALLET_NO_PUSH=1 python3 sp500_wallet.py   # dry run: update files, don't push
```

### Schedule it — GitHub Actions

The workflow **[`.github/workflows/sp500-wallet.yml`](.github/workflows/sp500-wallet.yml)**
runs it daily at `00:00 UTC` (≈ **03:00 Israel time** in summer / 02:00 in
winter — change the `cron` to shift it) and can also be run on demand from the
**Actions** tab (**Run workflow**).

> **To turn the daily schedule on, this branch must be merged into `main`** —
> GitHub only runs `schedule`/`workflow_dispatch` workflows from the default
> branch. Until then, run the script locally or trigger the workflow manually.

Scheduled workflows are also auto-disabled after ~60 days with no repo
activity; the daily commits from this and the questions routine keep it alive.
