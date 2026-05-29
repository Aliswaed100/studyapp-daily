#!/usr/bin/env python3
"""
Daily question generator for the studyapp Mac app.

Each run asks Claude for one brand-new flashcard/multiple-choice question on a
topic, appends it to `daily.json` (a deck export in the app's own format), and
pushes the change to GitHub. The Mac app downloads `daily.json` and merges the
new question into its "Daily Questions" deck.

Dependency-free: uses only the Python standard library (urllib), so it runs
anywhere Python 3 exists — no `pip install` needed.

Environment variables:
  ANTHROPIC_API_KEY   (required)  your Anthropic API key
  STUDYAPP_TOPIC      (optional)  subject for generated questions
                                  default: "Swift programming"
  STUDYAPP_MODEL      (optional)  Claude model id
                                  default: "claude-sonnet-4-5"
  STUDYAPP_NO_PUSH    (optional)  set to "1" to skip git commit/push (dry run)
"""

import json
import os
import sys
import uuid
import subprocess
import datetime
import urllib.request
import urllib.error
import pathlib

HERE = pathlib.Path(__file__).resolve().parent
FEED = HERE / "daily.json"

TOPIC = os.environ.get("STUDYAPP_TOPIC", "Swift programming")
MODEL = os.environ.get("STUDYAPP_MODEL", "claude-sonnet-4-5")
API_KEY = os.environ.get("ANTHROPIC_API_KEY", "").strip()


def fail(msg: str) -> None:
    print(f"[daily] ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def iso_now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_feed() -> dict:
    if not FEED.exists():
        fail(f"{FEED} not found. Run from inside the studyapp-daily repo.")
    return json.loads(FEED.read_text())


def recent_questions(feed: dict, n: int = 12) -> list[str]:
    return [q["questionText"] for q in feed.get("questions", [])[-n:]]


def ask_claude(avoid: list[str]) -> dict:
    if not API_KEY:
        fail("ANTHROPIC_API_KEY is not set.")

    avoid_block = "\n".join(f"- {t}" for t in avoid) or "(none yet)"
    prompt = f"""You are writing ONE new study flashcard about: {TOPIC}.

Return ONLY a JSON object (no markdown, no prose) with EXACTLY these keys:
  "questionText": string,
  "answerText": string,
  "hint": string (a short nudge, may be ""),
  "type": "flashcard" OR "multipleChoice",
  "options": array of strings (EMPTY for flashcard; 3-4 items for multipleChoice),
  "difficulty": "easy" OR "medium" OR "hard",
  "tags": array of 1-3 short lowercase strings.

Rules:
- If type is "multipleChoice", "answerText" MUST be exactly equal to one of "options".
- Keep questionText under 200 characters.
- Make it genuinely new — do NOT repeat any of these recent questions:
{avoid_block}
"""

    body = json.dumps({
        "model": MODEL,
        "max_tokens": 1024,
        "messages": [{"role": "user", "content": prompt}],
    }).encode("utf-8")

    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=body,
        headers={
            "content-type": "application/json",
            "x-api-key": API_KEY,
            "anthropic-version": "2023-06-01",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            payload = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        fail(f"Anthropic API HTTP {e.code}: {e.read().decode(errors='replace')}")
    except urllib.error.URLError as e:
        fail(f"Network error reaching Anthropic API: {e.reason}")

    try:
        text = payload["content"][0]["text"].strip()
    except (KeyError, IndexError):
        fail(f"Unexpected API response shape: {json.dumps(payload)[:400]}")

    # Strip accidental ```json fences.
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip().rstrip("`").strip()

    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        fail(f"Model did not return valid JSON:\n{text[:400]}")

    return obj


def validate(obj: dict) -> dict:
    qtype = obj.get("type")
    if qtype not in ("flashcard", "multipleChoice"):
        fail(f"Bad type: {qtype!r}")
    options = obj.get("options") or []
    answer = (obj.get("answerText") or "").strip()
    if qtype == "multipleChoice":
        if len(options) < 2 or answer not in options:
            fail("multipleChoice needs >=2 options and answerText must match one option.")
    else:
        options = []
    if not (obj.get("questionText") or "").strip():
        fail("Empty questionText.")
    diff = obj.get("difficulty", "medium")
    if diff not in ("easy", "medium", "hard"):
        diff = "medium"
    return {
        "questionText": obj["questionText"].strip(),
        "answerText": answer,
        "hint": (obj.get("hint") or "").strip(),
        "type": qtype,
        "options": options,
        "difficulty": diff,
        "tags": [str(t).strip() for t in (obj.get("tags") or []) if str(t).strip()][:3],
    }


def append_question(feed: dict, q: dict) -> dict:
    next_sort = max((qq.get("sortIndex", -1) for qq in feed["questions"]), default=-1) + 1
    feed["questions"].append({
        "id": str(uuid.uuid4()).upper(),
        "questionText": q["questionText"],
        "answerText": q["answerText"],
        "hint": q["hint"],
        "type": q["type"],
        "options": q["options"],
        "difficulty": q["difficulty"],
        "tags": q["tags"],
        "createdAt": iso_now(),
        "sortIndex": next_sort,
        "imageData": None,
        "isStarred": None,
    })
    return feed


def git_push() -> None:
    if os.environ.get("STUDYAPP_NO_PUSH") == "1":
        print("[daily] STUDYAPP_NO_PUSH=1 — skipping git push.")
        return
    today = datetime.date.today().isoformat()
    subprocess.run(["git", "-C", str(HERE), "add", "daily.json"], check=True)
    subprocess.run(
        ["git", "-C", str(HERE), "commit", "-m", f"Daily question for {today}"],
        check=True,
    )
    subprocess.run(["git", "-C", str(HERE), "push"], check=True)


def main() -> None:
    feed = load_feed()
    obj = ask_claude(recent_questions(feed))
    q = validate(obj)
    feed = append_question(feed, q)
    FEED.write_text(json.dumps(feed, indent=2) + "\n")
    print(f"[daily] added: {q['questionText']}")
    print(f"[daily] feed now has {len(feed['questions'])} questions.")
    git_push()
    print("[daily] done.")


if __name__ == "__main__":
    main()
