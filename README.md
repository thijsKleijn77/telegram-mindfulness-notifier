# Telegram Mindfulness Notifier

Five one-way mindfulness prompts a day, at random times between **07:30 and 23:00**
local time, delivered to Telegram. No replies, no conversation. Fire and forget.

It runs entirely on GitHub's servers, so your laptop can be shut in a bag.
Free forever: GitHub Actions on a public repo, the Telegram Bot API, and the
Python standard library. Nothing else.

---

## Setup, once

### 1. Create the bot

1. Open Telegram and message **@BotFather**.
2. Send `/newbot`.
3. Pick a display name, then a username ending in `bot`.
4. Copy the token it gives you. It looks like `123456789:AAH...`. Keep it private.

### 2. Get your chat id

1. Send any message ("hi") to your new bot in Telegram. This step is required,
   a bot cannot message you until you message it first.
2. Open this in a browser, pasting your token in place of `<TOKEN>`:
   `https://api.telegram.org/bot<TOKEN>/getUpdates`
3. Find `"chat":{"id":123456789` in the response. That number is your chat id.

### 3. Push this code to a new **public** GitHub repo

Public keeps Actions minutes unlimited. Your token is never in the code, it
lives in an encrypted Actions secret, so a public repo is both simpler and safer.

```bash
git remote add origin https://github.com/<you>/<repo>.git
git branch -M main
git push -u origin main
```

### 4. Add the two secrets

Repo > **Settings** > **Secrets and variables** > **Actions** > **New repository secret**:

| Name | Value |
| --- | --- |
| `TELEGRAM_BOT_TOKEN` | the token from BotFather |
| `TELEGRAM_CHAT_ID` | the number from step 2 |

### 5. Set your timezone

Open `notifier.py` and edit the `TIMEZONE` line near the top. Use an IANA name:
`Europe/Amsterdam`, `America/Bogota`, `Asia/Bangkok`, `America/New_York`.

### 6. Turn it on and test it

1. Repo > **Actions** tab > click **I understand my workflows, enable them**.
2. Pick **Mindfulness Notifier** in the left sidebar.
3. **Run workflow** > leave *Send one question immediately* ticked > **Run workflow**.
4. A message should land in Telegram inside a minute.

From then on it polls every 10 minutes on its own and sends 5 prompts a day.

---

## Living with it

### Editing the questions

Edit `questions.txt`, one prompt per line, straight from the GitHub mobile app
or the web editor. No code change, no redeploy.

* Lines starting with `#` and blank lines are ignored.
* A question you **delete** leaves the current deck immediately.
* A question you **add** joins the deck the next time it reshuffles, within a
  couple of days.

### Moving country

Change the one `TIMEZONE` line in `notifier.py` and commit. The rest of today
gets replanned in the new zone without resending anything you already got.

If you would rather not touch code: add a repository **variable** (not secret)
named `TIMEZONE` under Settings > Secrets and variables > Actions > Variables.
It overrides the file.

### Testing any time

Actions > Mindfulness Notifier > **Run workflow** with test mode ticked. It
ignores the schedule, sends the next question in the deck, and confirms the
wiring end to end.

### Keeping it alive

GitHub disables a scheduled workflow after **60 days without repo activity**.
The daily state commits count as activity, so it re-arms itself automatically.
If you ever pause it for months, push any single commit to wake it back up.

---

## How it works

GitHub cron is UTC only and cannot do random times, so the workflow is a dumb
10-minute heartbeat and `notifier.py` holds all the intelligence.

On every run:

1. **Plan.** If there is no plan for today's *local* date, generate one: five
   random times inside 07:30 to 23:00, at least 45 minutes apart, spread
   uniformly across every valid arrangement. Save it.
2. **Send.** Take the oldest due-but-unsent time and send its message. **At most
   one per run.** If several are due at once, after an outage say, the rest wait
   for the next poll, so you get a drip rather than a burst.
3. **Persist.** Commit the state files back to the repo, because Actions runners
   are wiped after every run.

Other decisions worth knowing:

* **Late is fine.** A time missed earlier today still gets sent. Once the local
  day rolls over, anything unsent is dropped and a fresh plan is made.
* **No long-running job.** The window is 15.5 hours and an Actions job is capped
  at 6, so a job that sleeps through the day is impossible. Hence the poller.
* **Cron drift.** Actions cron often fires several minutes late. The script never
  assumes exact timing, it only ever asks "what is due now".
* **Deck, not dice.** Questions come from a shuffled deck that is popped from the
  front, so nothing repeats until the whole set has been used. On reshuffle, the
  new first question is never the one you just got.
* **First run of the day happens late?** Planning starts from *now* rather than
  07:30, so a mid-afternoon install does not fire a backlog at you. It plans as
  many sends as still fit at the 45 minute gap.

### Files

| Path | What it is |
| --- | --- |
| `questions.txt` | your prompts, one per line, human editable |
| `notifier.py` | all the logic, standard library only |
| `.github/workflows/notifier.yml` | the 10-minute poller plus the manual test button |
| `state/schedule.json` | today's local date, the planned times, a sent flag each |
| `state/deck.json` | the remaining shuffled questions and the last one sent |

`state/` is written by the bot. You never need to edit it, and deleting either
file is harmless, it rebuilds on the next run.

### Costs

Nothing. A public repo has unlimited Actions minutes. For the record, if you ever
made it private, 10-minute polling is roughly 950 minutes a month against the
2,000 minute free tier, so it would still fit.
