#!/usr/bin/env python3
"""
Telegram mindfulness notifier.

One-way only. Sends a single mindfulness prompt per poll run, at 5 random
times a day inside a local-time window. No replies, no webhook, no polling
for updates. Standard library only.

Runs on a GitHub Actions cron every 10 minutes. Each run:
  1. Makes today's plan if there isn't one yet (5 random times, min 45 min apart).
  2. Sends the single oldest due-but-unsent message, if any.
  3. Persists state back to the repo so the next ephemeral runner can read it.
"""

from __future__ import annotations

import json
import os
import random
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------

# IANA timezone name, for example Europe/Amsterdam, America/Bogota, Asia/Bangkok.
#
#   >>> CHANGE THIS LINE WHEN YOU RELOCATE. <<<
#
# It is the only place the timezone is defined. Every window calculation below
# is done in this zone and only converted to UTC at the network boundary.
# You can also override it without editing code by adding a repository variable
# named TIMEZONE under Settings > Secrets and variables > Actions > Variables.
TIMEZONE = os.environ.get("TIMEZONE") or "Europe/Amsterdam"

WINDOW_START = time(7, 30)     # earliest possible send, local time
WINDOW_END = time(23, 0)       # latest possible send, local time
SENDS_PER_DAY = 5
MIN_GAP_MINUTES = 45           # minimum spacing between two sends

# Overridable so the wiring can be tested against a local stub server.
API_BASE = os.environ.get("TELEGRAM_API_BASE") or "https://api.telegram.org"

ROOT = Path(__file__).resolve().parent
QUESTIONS_FILE = ROOT / "questions.txt"
STATE_DIR = ROOT / "state"
SCHEDULE_FILE = STATE_DIR / "schedule.json"
DECK_FILE = STATE_DIR / "deck.json"

TRUTHY = {"1", "true", "yes", "on"}


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def fail(message: str) -> None:
    """Print to stderr and exit non-zero so the Actions run goes red."""
    print(f"ERROR: {message}", file=sys.stderr)
    sys.exit(1)


def zone() -> ZoneInfo:
    try:
        return ZoneInfo(TIMEZONE)
    except Exception:
        fail(f"Unknown timezone {TIMEZONE!r}. Use an IANA name like Europe/Amsterdam.")
        raise  # unreachable, keeps type checkers happy


def load_questions() -> list[str]:
    if not QUESTIONS_FILE.exists():
        fail(f"{QUESTIONS_FILE.name} is missing.")
    seen: set[str] = set()
    questions: list[str] = []
    for raw in QUESTIONS_FILE.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line in seen:
            continue
        seen.add(line)
        questions.append(line)
    if not questions:
        fail(f"{QUESTIONS_FILE.name} contains no questions.")
    return questions


def read_json(path: Path):
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        print(f"WARNING: {path.name} is unreadable, rebuilding it.")
        return None


def write_json(path: Path, data) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Planning
# ---------------------------------------------------------------------------

def build_plan(day, now=None, rng=random, count: int = SENDS_PER_DAY) -> list[datetime]:
    """
    Pick `count` random local times inside today's window, at least
    MIN_GAP_MINUTES apart, uniformly spread across every valid arrangement.

    If `now` is already past the window start (first install of the day, a
    relocation, a long outage) planning starts from `now` instead, so a fresh
    plan never dumps a backlog of already-past times. If the rest of the day
    cannot fit `count` sends at the minimum gap, fewer are planned.
    """
    tz = zone()
    start = datetime.combine(day, WINDOW_START)
    end = datetime.combine(day, WINDOW_END)

    if now is not None:
        now_local = now.replace(tzinfo=None, second=0, microsecond=0)
        if now_local > start:
            start = now_local

    span = int((end - start).total_seconds() // 60)
    if count < 1 or span < 0:
        return []

    n = min(count, SENDS_PER_DAY)
    while n > 1 and (n - 1) * MIN_GAP_MINUTES > span:
        n -= 1

    free = span - (n - 1) * MIN_GAP_MINUTES
    offsets = sorted(rng.randint(0, free) for _ in range(n))
    return [
        (start + timedelta(minutes=offsets[i] + i * MIN_GAP_MINUTES)).replace(tzinfo=tz)
        for i in range(n)
    ]


def new_schedule(now: datetime, rng=random, already_sent: int = 0) -> dict:
    plan = build_plan(now.date(), now=now, rng=rng, count=SENDS_PER_DAY - already_sent)
    return {
        "date": now.date().isoformat(),
        "timezone": TIMEZONE,
        "generated_at": now.isoformat(timespec="seconds"),
        "sends": [
            {"at": t.isoformat(timespec="seconds"), "sent": False, "sent_at": None, "question": None}
            for t in plan
        ],
    }


# ---------------------------------------------------------------------------
# Deck
# ---------------------------------------------------------------------------

def load_deck() -> dict:
    deck = read_json(DECK_FILE)
    if not isinstance(deck, dict):
        deck = {}
    remaining = deck.get("remaining")
    return {
        "remaining": [q for q in remaining if isinstance(q, str)] if isinstance(remaining, list) else [],
        "last_sent": deck.get("last_sent") if isinstance(deck.get("last_sent"), str) else None,
    }


def take_question(deck: dict, questions: list[str], rng=random) -> tuple[str, dict]:
    """
    Pop the next question off the shuffled deck. Reshuffles the full set when
    empty, avoiding an immediate repeat of the last question sent. Questions
    deleted from questions.txt drop out of the current deck straight away.
    """
    valid = set(questions)
    remaining = [q for q in deck.get("remaining", []) if q in valid]

    if not remaining:
        remaining = list(questions)
        rng.shuffle(remaining)
        if len(remaining) > 1 and remaining[0] == deck.get("last_sent"):
            swap = rng.randrange(1, len(remaining))
            remaining[0], remaining[swap] = remaining[swap], remaining[0]
        print(f"Deck reshuffled, {len(remaining)} questions.")

    question = remaining.pop(0)
    return question, {"remaining": remaining, "last_sent": question}


# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------

def send_message(text: str) -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        fail("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be set as Actions secrets.")

    url = f"{API_BASE}/bot{token}/sendMessage"
    payload = urllib.parse.urlencode({"chat_id": chat_id, "text": text}).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=payload,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = response.read().decode("utf-8", "replace")
            if response.status != 200:
                fail(f"Telegram returned HTTP {response.status}: {body}")
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", "replace")
        fail(f"Telegram returned HTTP {error.code}: {body}")
    except urllib.error.URLError as error:
        fail(f"Could not reach Telegram: {error.reason}")


# ---------------------------------------------------------------------------
# The poll run
# ---------------------------------------------------------------------------

def run_once(now: datetime | None = None, rng=random) -> None:
    tz = zone()
    now = now or datetime.now(tz)
    questions = load_questions()

    test_mode = os.environ.get("TEST_MODE", "").strip().lower() in TRUTHY or "--test" in sys.argv
    if test_mode:
        question, deck = take_question(load_deck(), questions, rng)
        send_message(question)
        write_json(DECK_FILE, deck)
        print(f"TEST MODE: sent {question!r}")
        return

    schedule = read_json(SCHEDULE_FILE)
    stale = (
        not isinstance(schedule, dict)
        or schedule.get("date") != now.date().isoformat()
        or schedule.get("timezone") != TIMEZONE
    )
    if stale:
        # A new local day drops any unsent times from yesterday. A timezone
        # change mid-day replans the rest of today without resending.
        already = 0
        if isinstance(schedule, dict) and schedule.get("date") == now.date().isoformat():
            already = sum(1 for s in schedule.get("sends", []) if s.get("sent"))
        schedule = new_schedule(now, rng=rng, already_sent=already)
        write_json(SCHEDULE_FILE, schedule)
        times = ", ".join(s["at"][11:16] for s in schedule["sends"]) or "none"
        print(f"New plan for {schedule['date']} ({TIMEZONE}): {times}")

    due = [
        s for s in schedule["sends"]
        if not s.get("sent") and datetime.fromisoformat(s["at"]) <= now
    ]
    if not due:
        upcoming = [s["at"][11:16] for s in schedule["sends"] if not s.get("sent")]
        print(f"Nothing due at {now.strftime('%H:%M')}. Still to come today: {', '.join(upcoming) or 'none'}")
        return

    # At most one message per run. Anything else that is due waits for the
    # next poll 10 minutes from now, so an outage drips instead of bursting.
    item = min(due, key=lambda s: s["at"])
    question, deck = take_question(load_deck(), questions, rng)

    send_message(question)  # exits non-zero on failure, before any state is saved

    item["sent"] = True
    item["sent_at"] = now.isoformat(timespec="seconds")
    item["question"] = question
    write_json(SCHEDULE_FILE, schedule)
    write_json(DECK_FILE, deck)

    late = int((now - datetime.fromisoformat(item["at"])).total_seconds() // 60)
    print(f"Sent {question!r} (planned {item['at'][11:16]}, {late} min late). {len(due) - 1} still queued.")


if __name__ == "__main__":
    run_once()
