# Grab Daily Telegram Bot

## Setup in 15 minutes — no command line required

You do **not** need to install Python or run anything on your Mac. Everything below is done in your browser (Telegram, GitHub, Render).

### Step 1 — Create your Telegram bot
1. Open Telegram, search for **@BotFather**
2. Send `/newbot`
3. Give it a name: `Grab Daily` and a username: `grabdaily_yourname_bot`
4. Copy the **token** it gives you — looks like `123456:ABCdef...`

### Step 2 — Get your group chat ID
1. Add your bot to your PR team group chat
2. Open this URL in your browser (replace `YOUR_TOKEN`):
   `https://api.telegram.org/botYOUR_TOKEN/getUpdates`
3. Send any message in the group chat
4. Refresh the URL — look for `"chat":{"id":` — copy that number (it will be negative, e.g. `-1001234567890`)

### Step 3 — Get a Perplexity API key (optional but recommended)
1. Go to [perplexity.ai](https://www.perplexity.ai) → API → sign up
2. Free tier gives $5 credits — enough for weeks of use
3. Copy your API key

### Step 4 — Put the code on GitHub (browser only)
1. Go to [github.com](https://github.com) and sign in
2. Click **New repository** → name it e.g. `grab-daily-bot` → **Create repository**
3. Click **Add file** → **Upload files**
4. Upload these files from this folder:
   - `bot.py`
   - `Procfile`
   - `requirements.txt`
   - `runtime.txt`
   - `render.yaml` (optional, for Blueprint deploy)
   - `README.md`
5. Click **Commit changes**

### Step 5 — Deploy on Render (browser only)

**Important:** This bot runs 24/7 in the background (Telegram polling). On Render you must use a **Background Worker**, not a Web Service.

#### Option A — Blueprint (easiest if you uploaded `render.yaml`)
1. Go to [render.com](https://render.com) and sign up
2. Click **New** → **Blueprint**
3. Connect your GitHub account and select the `grab-daily-bot` repo
4. Render will read `render.yaml` and create the worker for you
5. When prompted, set environment variables:
   - `TELEGRAM_TOKEN` = token from Step 1
   - `GROUP_CHAT_ID` = chat ID from Step 2
   - `PERPLEXITY_KEY` = key from Step 3 (leave empty if skipping)
6. Click **Apply** / **Deploy**

#### Option B — Manual worker setup
1. Go to [render.com](https://render.com) and sign up
2. Click **New** → **Background Worker** (not Web Service)
3. Connect your GitHub repo from Step 4
4. Render should detect the `Procfile` automatically. If it asks for commands:
   - **Build command:** `pip install -r requirements.txt`
   - **Start command:** `python bot.py`
5. Under **Environment**, add:
   - `TELEGRAM_TOKEN` = token from Step 1
   - `GROUP_CHAT_ID` = chat ID from Step 2
   - `PERPLEXITY_KEY` = key from Step 3 (optional)
6. Click **Create Background Worker** / **Deploy**

#### After deploy
- In the Render dashboard, open your worker → **Logs**. You should see `Bot started. Polling...`
- If the worker crashes immediately, double-check `TELEGRAM_TOKEN` and `GROUP_CHAT_ID`

**Note on free tier:** Render’s free Background Worker plan may not be available in all accounts/regions. If free isn’t offered, the smallest paid worker plan keeps the bot running 24/7.

**Note on saved data:** Article history is stored in `state.json` on the server. On redeploy, that file may reset unless you add a Render persistent disk (optional; not required to get started).

### Step 6 — Test it
In Telegram, message your bot (in a private chat or the group): send `/all`. Within about 30 seconds, new articles should appear in your group chat.

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

## Files in this project
| File | Purpose |
|------|---------|
| `bot.py` | Main bot code |
| `Procfile` | Tells Render to run `python bot.py` as a worker |
| `requirements.txt` | Python packages |
| `runtime.txt` | Python version for Render |
| `render.yaml` | Optional Blueprint config for Render |
