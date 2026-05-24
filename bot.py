"""
Grab Daily Telegram Bot
-----------------------
Fetches Grab / competitor / industry news via RSS + Perplexity,
posts to a Telegram group chat, and drafts the 12pm report.

Commands:
  /grab       - fetch latest Grab articles now
  /comp       - fetch competitor articles now
  /industry   - fetch industry articles now
  /all        - fetch all three buckets now
  /report     - draft today's 12pm Grab Daily report
  /watchlist  - show active watchlist items
  /watch <keyword> | <notes> | <days>  - add a watchlist item
  /help       - show commands

Schedules (Singapore time):
  08:00, 10:00, 12:00, 15:00, 17:00, 19:00 - auto-fetch all buckets
  12:00 - also sends draft report to group
"""

import os
import json
import logging
import asyncio
import feedparser
import httpx
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, ContextTypes
)
from telegram.constants import ParseMode

# ── Config ────────────────────────────────────────────────────────────────────
TELEGRAM_TOKEN   = os.environ["TELEGRAM_TOKEN"]        # from BotFather
GROUP_CHAT_ID    = int(os.environ["GROUP_CHAT_ID"])    # group chat ID (negative int)
PERPLEXITY_KEY   = os.environ.get("PERPLEXITY_KEY", "") # optional but recommended
SGT              = ZoneInfo("Asia/Singapore")

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    level=logging.INFO
)
log = logging.getLogger(__name__)

# ── State (in-memory, survives restarts via JSON file) ────────────────────────
STATE_FILE = "state.json"

def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {"articles": [], "watchlist": [], "next_id": 1}

def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)

state = load_state()

def prune_old():
    """Remove articles older than 14 days."""
    cutoff = (datetime.now(SGT) - timedelta(days=14)).isoformat()
    state["articles"] = [a for a in state["articles"] if a["date"] >= cutoff[:10]]
    save_state(state)

def add_article(headline, url, pub, bucket, urgent=False):
    """Add article if URL not already present. Returns True if added."""
    if any(a["url"] == url for a in state["articles"]):
        return False
    state["articles"].append({
        "id": state["next_id"],
        "bucket": bucket,
        "pub": pub,
        "headline": headline,
        "url": url,
        "date": datetime.now(SGT).strftime("%Y-%m-%d"),
        "urgent": urgent,
        "selected": False,
    })
    state["next_id"] += 1
    save_state(state)
    return True

# ── Keywords ──────────────────────────────────────────────────────────────────
GRAB_KW  = ["grab","grabfood","grabcar","grabmart","grabpay","gxs bank","ai.r","grabcab","grabtaxi","grabinsure","grabads","grab holdings"]
COMP_KW  = ["foodpanda","gojek","comfortdelgro","tada ","ryde ","shopee","fairprice","dbs paylah","lalamove","deliveroo","geolah","atome","maribank","cdg zig"]
IND_KW   = ["land transport authority","lta ","lta,","mrt ","mrt,","coe ","autonomous vehicle","platform worker","erp ","erp2","rts link","ev charging","electric vehicle","public transport"]

def detect_bucket(text):
    t = " " + text.lower() + " "
    if any(k in t for k in GRAB_KW):  return "grab"
    if any(k in t for k in COMP_KW):  return "comp"
    if any(k in t for k in IND_KW):   return "ind"
    return None

def is_urgent(headline):
    urgent_triggers = ["suspend","ban","jailed","arrested","convicted","crash","accident","death","dies","killed","scam","fraud","cheat","fine","charged","court","recall","outage","disruption"]
    h = headline.lower()
    return any(t in h for t in urgent_triggers) and "grab" in h.lower()

# ── RSS Feeds ─────────────────────────────────────────────────────────────────
RSS_FEEDS = [
    {"name": "The Straits Times",   "url": "https://www.straitstimes.com/news/singapore/rss.xml"},
    {"name": "The Business Times",  "url": "https://www.businesstimes.com.sg/rss/singapore"},
    {"name": "CNA",                 "url": "https://www.channelnewsasia.com/api/v1/rss-outbound-feed?_format=xml&category=10416"},
    {"name": "Mothership",          "url": "https://mothership.sg/feed"},
    {"name": "AsiaOne",             "url": "https://www.asiaone.com/rss/latest"},
    {"name": "MustShareNews",       "url": "https://mustsharenews.com/feed"},
    {"name": "The Edge Singapore",  "url": "https://www.theedgesingapore.com/rss.xml"},
    {"name": "STOMP",               "url": "https://www.stomp.sg/rss"},
]

async def fetch_rss():
    """Fetch all RSS feeds. Returns list of (headline, url, pub, bucket)."""
    found = []
    async with httpx.AsyncClient(timeout=15) as client:
        for feed in RSS_FEEDS:
            try:
                resp = await client.get(feed["url"])
                parsed = feedparser.parse(resp.text)
                for entry in parsed.entries[:20]:
                    title = entry.get("title","").strip()
                    link  = entry.get("link","").strip()
                    if not title or not link: continue
                    bucket = detect_bucket(title + " " + link)
                    if bucket:
                        found.append((title, link, feed["name"], bucket))
            except Exception as e:
                log.warning(f"RSS error {feed['name']}: {e}")
    return found

# ── Perplexity Search ─────────────────────────────────────────────────────────
PERPLEXITY_QUERIES = {
    "grab": 'Grab Singapore news today site:straitstimes.com OR site:channelnewsasia.com OR site:businesstimes.com.sg OR site:mothership.sg OR site:stomp.sg OR site:mustsharenews.com OR site:asiaone.com',
    "comp": 'Foodpanda OR Gojek OR ComfortDelGro OR Shopee Singapore news today site:straitstimes.com OR site:channelnewsasia.com OR site:businesstimes.com.sg OR site:mothership.sg',
    "ind":  'Singapore transport LTA MRT COE autonomous vehicle platform workers news today site:straitstimes.com OR site:channelnewsasia.com OR site:businesstimes.com.sg',
}

async def fetch_perplexity(bucket):
    """Search Perplexity for articles. Returns list of (headline, url, pub, bucket)."""
    if not PERPLEXITY_KEY:
        return []
    query = PERPLEXITY_QUERIES.get(bucket, "")
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                "https://api.perplexity.ai/chat/completions",
                headers={
                    "Authorization": f"Bearer {PERPLEXITY_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "sonar",
                    "messages": [{
                        "role": "user",
                        "content": f"Find the latest Singapore news articles today about: {query}. Return a JSON array only, no other text. Each item: {{\"headline\": \"...\", \"url\": \"...\", \"pub\": \"publication name\"}}. Max 8 items."
                    }],
                    "return_citations": True,
                }
            )
            data = resp.json()
            raw = data["choices"][0]["message"]["content"]
            raw = raw.replace("```json","").replace("```","").strip()
            items = json.loads(raw)
            found = []
            for item in items:
                h = item.get("headline","").strip()
                u = item.get("url","").strip()
                p = item.get("pub","Unknown")
                if h and u:
                    found.append((h, u, p, bucket))
            return found
    except Exception as e:
        log.warning(f"Perplexity error ({bucket}): {e}")
        return []

# ── Core fetch logic ──────────────────────────────────────────────────────────
async def fetch_bucket(bucket):
    """Fetch RSS + Perplexity for a bucket. Returns new articles added."""
    prune_old()
    rss_all = await fetch_rss()
    rss_bucket = [(h,u,p,b) for h,u,p,b in rss_all if b == bucket]
    perp = await fetch_perplexity(bucket)
    combined = rss_bucket + perp

    new_articles = []
    urgent_articles = []
    for headline, url, pub, bkt in combined:
        added = add_article(headline, url, pub, bkt, urgent=is_urgent(headline))
        if added:
            art = state["articles"][-1]
            new_articles.append(art)
            if art["urgent"]:
                urgent_articles.append(art)

    return new_articles, urgent_articles

async def fetch_all_buckets():
    """Fetch all three buckets. Returns combined new + urgent."""
    all_new, all_urgent = [], []
    for bucket in ["grab","comp","ind"]:
        new, urgent = await fetch_bucket(bucket)
        all_new.extend(new)
        all_urgent.extend(urgent)
    return all_new, all_urgent

# ── Telegram message formatting ───────────────────────────────────────────────
BUCKET_EMOJI = {"grab": "🟢", "comp": "🟠", "ind": "🔵"}
BUCKET_LABEL = {"grab": "Grab", "comp": "Competitor", "ind": "Industry"}

def fmt_article_msg(art):
    urgent_flag = "🚨 URGENT\n" if art["urgent"] else ""
    emoji = BUCKET_EMOJI.get(art["bucket"],"•")
    return (
        f"{urgent_flag}{emoji} *{art['pub']}*\n"
        f"[{art['headline']}]({art['url']})\n"
        f"_{art['date']}_"
    )

def fmt_sweep_summary(new_articles, bucket_label):
    today = datetime.now(SGT).strftime("%-d %B %Y")
    if not new_articles:
        return f"*{bucket_label} sweep — {today}*\nNo new articles found."
    lines = [f"*{bucket_label} sweep — {today}*\n_{len(new_articles)} new article(s)_\n"]
    for art in new_articles:
        emoji = BUCKET_EMOJI.get(art["bucket"],"•")
        urg = "🚨 " if art["urgent"] else ""
        lines.append(f"{urg}{emoji} [{art['headline']}]({art['url']}) — _{art['pub']}_")
    return "\n".join(lines)

def fmt_report():
    """Format today's selected articles as Grab Daily bullets."""
    today = datetime.now(SGT).strftime("%-d %B %Y")
    today_arts = [a for a in state["articles"] if a["date"] == datetime.now(SGT).strftime("%Y-%m-%d")]

    grab = [a for a in today_arts if a["bucket"] == "grab"]
    comp = [a for a in today_arts if a["bucket"] == "comp"]
    ind  = [a for a in today_arts if a["bucket"] == "ind"]

    lines = [
        f"📋 *The Grab Daily — {today}*",
        f"_Subject: The Grab Daily - Daily Monitoring {today}_\n",
        "Hi all,\n\nPlease find today's Grab Daily report below:\n",
    ]

    if grab:
        lines.append("*Grab*")
        for a in grab:
            lines.append(f"• [{a['headline']}]({a['url']}) _{a['pub']}_")
        lines.append("")

    if comp:
        lines.append("*Competitor News*")
        for a in comp:
            lines.append(f"• [{a['headline']}]({a['url']}) _{a['pub']}_")
        lines.append("")

    if ind:
        lines.append("*Industry News*")
        for a in ind:
            lines.append(f"• [{a['headline']}]({a['url']}) _{a['pub']}_")
        lines.append("")

    if not grab and not comp and not ind:
        lines.append("_No articles fetched yet today. Run /all first._")

    return "\n".join(lines)

def fmt_watchlist():
    now = datetime.now(SGT)
    active = [w for w in state["watchlist"] if datetime.fromisoformat(w["due"]) >= now]
    if not active:
        return "📋 *Watchlist*\nNo active items."
    lines = ["📋 *Watchlist — active items*\n"]
    for w in active:
        due = datetime.fromisoformat(w["due"])
        days = (due - now).days
        matched = any(w["keyword"].lower().split()[0] in a["headline"].lower() for a in state["articles"])
        status = "✅ Matched" if matched else f"👁 Watching ({days}d left)"
        lines.append(f"*{w['keyword']}* — {status}")
        if w.get("notes"): lines.append(f"  _{w['notes']}_")
    return "\n".join(lines)

# ── Telegram command handlers ─────────────────────────────────────────────────
async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = (
        "🤖 *Grab Daily Bot*\n\n"
        "/grab — fetch Grab articles now\n"
        "/comp — fetch competitor articles now\n"
        "/industry — fetch industry articles now\n"
        "/all — fetch all three buckets\n"
        "/report — draft today's 12pm report\n"
        "/watchlist — show active watchlist\n"
        "/watch keyword | notes | days — add watchlist item\n"
        "  e.g. `/watch Grab AV Punggol | Expected follow-up | 7`\n\n"
        "⏰ *Auto-schedule (SGT):* 8am, 10am, 12pm, 3pm, 5pm, 7pm\n"
        "📋 *Report draft:* sent to group at 12pm daily"
    )
    await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)

async def cmd_grab(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔍 Fetching Grab articles...")
    new, urgent = await fetch_bucket("grab")
    msg = fmt_sweep_summary(new, "Grab")
    await ctx.bot.send_message(GROUP_CHAT_ID, msg, parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True)
    for art in urgent:
        await ctx.bot.send_message(GROUP_CHAT_ID, f"🚨 *URGENT — Grab mention*\n[{art['headline']}]({art['url']})\n_{art['pub']}_", parse_mode=ParseMode.MARKDOWN)

async def cmd_comp(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔍 Fetching competitor articles...")
    new, urgent = await fetch_bucket("comp")
    msg = fmt_sweep_summary(new, "Competitors")
    await ctx.bot.send_message(GROUP_CHAT_ID, msg, parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True)

async def cmd_industry(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔍 Fetching industry articles...")
    new, urgent = await fetch_bucket("ind")
    msg = fmt_sweep_summary(new, "Industry")
    await ctx.bot.send_message(GROUP_CHAT_ID, msg, parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True)

async def cmd_all(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔍 Fetching all buckets...")
    new, urgent = await fetch_all_buckets()
    grab_new = [a for a in new if a["bucket"]=="grab"]
    comp_new  = [a for a in new if a["bucket"]=="comp"]
    ind_new   = [a for a in new if a["bucket"]=="ind"]
    summary = (
        f"🗞 *Full sweep — {datetime.now(SGT).strftime('%-d %B %Y %H:%M')} SGT*\n"
        f"🟢 Grab: {len(grab_new)} new\n"
        f"🟠 Competitors: {len(comp_new)} new\n"
        f"🔵 Industry: {len(ind_new)} new\n"
    )
    await ctx.bot.send_message(GROUP_CHAT_ID, summary, parse_mode=ParseMode.MARKDOWN)
    for art in new[:15]:
        emoji = BUCKET_EMOJI.get(art["bucket"],"•")
        urg = "🚨 " if art["urgent"] else ""
        await ctx.bot.send_message(
            GROUP_CHAT_ID,
            f"{urg}{emoji} [{art['headline']}]({art['url']})\n_{art['pub']}_",
            parse_mode=ParseMode.MARKDOWN,
            disable_web_page_preview=True
        )
    for art in urgent:
        await ctx.bot.send_message(
            GROUP_CHAT_ID,
            f"🚨 *URGENT — {art['pub']}*\n[{art['headline']}]({art['url']})",
            parse_mode=ParseMode.MARKDOWN
        )

async def cmd_report(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = fmt_report()
    await ctx.bot.send_message(GROUP_CHAT_ID, msg, parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True)

async def cmd_watchlist(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(fmt_watchlist(), parse_mode=ParseMode.MARKDOWN)

async def cmd_watch(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.replace("/watch","").strip()
    parts = [p.strip() for p in text.split("|")]
    if not parts or not parts[0]:
        await update.message.reply_text("Usage: /watch keyword | notes | days\nExample: /watch Grab AV Punggol | Expected follow-up | 7")
        return
    keyword = parts[0]
    notes   = parts[1] if len(parts) > 1 else ""
    days    = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 7
    due = (datetime.now(SGT) + timedelta(days=days)).isoformat()
    state["watchlist"].append({"keyword": keyword, "notes": notes, "due": due})
    save_state(state)
    await update.message.reply_text(f"✅ Watching: *{keyword}*\nUntil: {(datetime.now(SGT)+timedelta(days=days)).strftime('%-d %b')}", parse_mode=ParseMode.MARKDOWN)

# ── Scheduled jobs ────────────────────────────────────────────────────────────
async def scheduled_sweep(app):
    log.info("Running scheduled sweep...")
    new, urgent = await fetch_all_buckets()
    if not new and not urgent:
        return
    grab_new = [a for a in new if a["bucket"]=="grab"]
    comp_new  = [a for a in new if a["bucket"]=="comp"]
    ind_new   = [a for a in new if a["bucket"]=="ind"]
    now_str = datetime.now(SGT).strftime("%H:%M")
    summary = (
        f"⏰ *{now_str} sweep*\n"
        f"🟢 Grab: {len(grab_new)} new  🟠 Comp: {len(comp_new)} new  🔵 Industry: {len(ind_new)} new"
    )
    await app.bot.send_message(GROUP_CHAT_ID, summary, parse_mode=ParseMode.MARKDOWN)
    for art in new[:10]:
        emoji = BUCKET_EMOJI.get(art["bucket"],"•")
        urg = "🚨 " if art["urgent"] else ""
        await app.bot.send_message(
            GROUP_CHAT_ID,
            f"{urg}{emoji} [{art['headline']}]({art['url']})\n_{art['pub']}_",
            parse_mode=ParseMode.MARKDOWN,
            disable_web_page_preview=True
        )
    for art in urgent:
        await app.bot.send_message(
            GROUP_CHAT_ID,
            f"🚨 *URGENT — {BUCKET_LABEL.get(art['bucket'],art['bucket'])}*\n[{art['headline']}]({art['url']})\n_{art['pub']}_",
            parse_mode=ParseMode.MARKDOWN
        )

async def scheduled_report(app):
    log.info("Sending 12pm report...")
    msg = fmt_report()
    await app.bot.send_message(GROUP_CHAT_ID, msg, parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True)

    # also check watchlist matches
    wl_msg = fmt_watchlist()
    matched = [w for w in state["watchlist"] if any(
        w["keyword"].lower().split()[0] in a["headline"].lower() for a in state["articles"]
        if a["date"] == datetime.now(SGT).strftime("%Y-%m-%d")
    )]
    if matched:
        kws = ", ".join(w["keyword"] for w in matched)
        await app.bot.send_message(GROUP_CHAT_ID, f"👁 *Watchlist matches today:* {kws}", parse_mode=ParseMode.MARKDOWN)

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("help",      cmd_help))
    app.add_handler(CommandHandler("start",     cmd_help))
    app.add_handler(CommandHandler("grab",      cmd_grab))
    app.add_handler(CommandHandler("comp",      cmd_comp))
    app.add_handler(CommandHandler("industry",  cmd_industry))
    app.add_handler(CommandHandler("all",       cmd_all))
    app.add_handler(CommandHandler("report",    cmd_report))
    app.add_handler(CommandHandler("watchlist", cmd_watchlist))
    app.add_handler(CommandHandler("watch",     cmd_watch))

    scheduler = AsyncIOScheduler(timezone=SGT)
    # sweeps at 8am, 10am, 12pm, 3pm, 5pm, 7pm SGT
    for hour in [8, 10, 15, 17, 19]:
        scheduler.add_job(scheduled_sweep, "cron", hour=hour, minute=0, args=[app])
    # 12pm: sweep + report
    scheduler.add_job(scheduled_sweep, "cron", hour=12, minute=0, args=[app])
    scheduler.add_job(scheduled_report, "cron", hour=12, minute=1, args=[app])

    scheduler.start()
    log.info("Bot started. Scheduler running. Waiting for commands...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
