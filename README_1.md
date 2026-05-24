# Grab Daily Telegram Bot

## Setup in 15 minutes — no developer needed

### Step 1 — Create your Telegram bot
1. Open Telegram, search for **@BotFather**
2. Send `/newbot`
3. Give it a name: `Grab Daily` and a username: `grabdaily_yourname_bot`
4. Copy the **token** it gives you — looks like `123456:ABCdef...`

### Step 2 — Get your group chat ID
1. Add your bot to your PR team group chat
2. Open this URL in your browser (replace YOUR_TOKEN):
   `https://api.telegram.org/botYOUR_TOKEN/getUpdates`
3. Send any message in the group chat
4. Refresh the URL — look for `"chat":{"id":` — copy that number (it will be negative, e.g. `-1001234567890`)

### Step 3 — Get a Perplexity API key (optional but recommended)
1. Go to perplexity.ai → API → sign up
2. Free tier gives $5 credits — enough for weeks of use
3. Copy your API key

### Step 4 — Deploy on Render (free)
1. Go to **render.com** and sign up free
2. Click **New → Web Service**
3. Connect your GitHub (or upload files directly)
4. Set environment variables:
   - `TELEGRAM_TOKEN` = your bot token from Step 1
   - `GROUP_CHAT_ID` = your group chat ID from Step 2
   - `PERPLEXITY_KEY` = your Perplexity key from Step 3 (leave blank if skipping)
5. Build command: `pip install -r requirements.txt`
6. Start command: `python bot.py`
7. Click **Deploy**

### Step 5 — Test it
In Telegram, send `/all` to your bot — it should post articles to your group chat within 30 seconds.

## Commands
| Command | What it does |
|---------|-------------|
| `/grab` | Fetch latest Grab articles now |
| `/comp` | Fetch competitor articles now |
| `/industry` | Fetch industry articles now |
| `/all` | Fetch all three buckets now |
| `/report` | Draft today's 12pm Grab Daily report |
| `/watchlist` | Show active watchlist items |
| `/watch keyword \| notes \| days` | Add a watchlist item |
| `/help` | Show all commands |

## Auto-schedule (Singapore time)
- **8:00am** — morning sweep, posts new articles to group
- **10:00am** — mid-morning sweep
- **12:00pm** — noon sweep + **sends draft report to group**
- **3:00pm** — afternoon sweep
- **5:00pm** — evening sweep
- **7:00pm** — final sweep

## Watchlist example
`/watch Grab AV Punggol | Expected ST follow-up after ATx announcement | 7`

This watches for any article containing "Grab" in the next 7 days and flags it at 12pm.

## What gets auto-detected as urgent
Any article mentioning Grab + these words: suspended, banned, jailed, arrested, crash, accident, scam, fraud, court, fine, outage — gets flagged 🚨 immediately.
