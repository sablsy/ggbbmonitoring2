"""
Grab Daily Telegram Bot
-----------------------
RSS + Perplexity news fetcher for Grab PR team.
Posts to Telegram group. Schedules sweeps 8am-7pm SGT.
Sends draft report at 12pm SGT.
"""

import os, json, logging, asyncio, feedparser, httpx
from datetime import datetime, timedelta, time
from zoneinfo import ZoneInfo
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from telegram.constants import ParseMode

# ── Config ────────────────────────────────────────────────────────────────────
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
GROUP_CHAT_ID  = int(os.environ["GROUP_CHAT_ID"])
PERPLEXITY_KEY = os.environ.get("PERPLEXITY_KEY", "")
SGT            = ZoneInfo("Asia/Singapore")

logging.basicConfig(format="%(asctime)s [%(levelname)s] %(message)s", level=logging.INFO)
log = logging.getLogger(__name__)

# ── State ─────────────────────────────────────────────────────────────────────
STATE_FILE = "state.json"

def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return {"articles": [], "watchlist": [], "next_id": 1}

def save_state(s):
    with open(STATE_FILE, "w") as f:
        json.dump(s, f, indent=2)

state = load_state()

def prune_old():
    cutoff = (datetime.now(SGT) - timedelta(days=14)).strftime("%Y-%m-%d")
    state["articles"] = [a for a in state["articles"] if a["date"] >= cutoff]
    save_state(state)

def add_article(headline, url, pub, bucket, urgent=False):
    if any(a["url"] == url for a in state["articles"]):
        return False
    state["articles"].append({
        "id": state["next_id"], "bucket": bucket, "pub": pub,
        "headline": headline, "url": url,
        "date": datetime.now(SGT).strftime("%Y-%m-%d"),
        "urgent": urgent,
    })
    state["next_id"] += 1
    save_state(state)
    return True

# ── Keywords ──────────────────────────────────────────────────────────────────
GRAB_KW = ["grab","grabfood","grabcar","grabmart","grabpay","gxs bank","ai.r","grabcab","grabtaxi","grabinsure","grabads","grab holdings"]
COMP_KW = ["foodpanda","gojek","comfortdelgro","tada ","ryde ","shopee","fairprice","dbs paylah","lalamove","deliveroo","geolah","atome","maribank","cdg zig"]
IND_KW  = ["land transport authority","lta ","lta,","mrt ","mrt,","coe ","autonomous vehicle","platform worker","erp ","rts link","ev charging","electric vehicle","public transport"]

def detect_bucket(text):
    t = " " + text.lower() + " "
    if any(k in t for k in GRAB_KW): return "grab"
    if any(k in t for k in COMP_KW): return "comp"
    if any(k in t for k in IND_KW):  return "ind"
    return None

def is_urgent(headline):
    triggers = ["suspend","ban","jailed","arrested","convicted","crash","accident","death","dies","killed","scam","fraud","cheat","fine","charged","court","recall","outage","disruption"]
    h = headline.lower()
    return "grab" in h and any(t in h for t in triggers)

# ── RSS ───────────────────────────────────────────────────────────────────────
RSS_FEEDS = [
    {"name": "The Straits Times",  "url": "https://www.straitstimes.com/news/singapore/rss.xml"},
    {"name": "The Business Times", "url": "https://www.businesstimes.com.sg/rss/singapore"},
    {"name": "CNA",                "url": "https://www.channelnewsasia.com/api/v1/rss-outbound-feed?_format=xml&category=10416"},
    {"name": "Mothership",         "url": "https://mothership.sg/feed"},
    {"name": "AsiaOne",            "url": "https://www.asiaone.com/rss/latest"},
    {"name": "MustShareNews",      "url": "https://mustsharenews.com/feed"},
    {"name": "The Edge Singapore", "url": "https://www.theedgesingapore.com/rss.xml"},
    {"name": "STOMP",              "url": "https://www.stomp.sg/rss"},
]

async def fetch_rss():
    found = []
    async with httpx.AsyncClient(timeout=15) as client:
        for feed in RSS_FEEDS:
            try:
                resp   = await client.get(feed["url"])
                parsed = feedparser.parse(resp.text)
                for entry in parsed.entries[:20]:
                    title  = entry.get("title", "").strip()
                    link   = entry.get("link", "").strip()
                    if not title or not link: continue
                    bucket = detect_bucket(title + " " + link)
                    if bucket:
                        found.append((title, link, feed["name"], bucket))
            except Exception as e:
                log.warning(f"RSS error {feed['name']}: {e}")
    return found

# ── Perplexity ────────────────────────────────────────────────────────────────
PERP_Q = {
    "grab": "Grab Singapore superapp news today from Straits Times CNA Business Times Mothership STOMP",
    "comp": "Foodpanda Gojek ComfortDelGro Shopee Singapore news today",
    "ind":  "Singapore LTA MRT COE transport platform workers news today",
}

async def fetch_perplexity(bucket):
    if not PERPLEXITY_KEY: return []
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                "https://api.perplexity.ai/chat/completions",
                headers={"Authorization": f"Bearer {PERPLEXITY_KEY}", "Content-Type": "application/json"},
                json={"model": "sonar", "messages": [{"role": "user", "content":
                    f"Find latest Singapore news today about: {PERP_Q[bucket]}. "
                    "Return ONLY a JSON array, no other text. Each item: "
                    "{\"headline\": \"...\", \"url\": \"...\", \"pub\": \"...\"} Max 6 items."}]}
            )
            raw   = resp.json()["choices"][0]["message"]["content"]
            raw   = raw.replace("```json","").replace("```","").strip()
            items = json.loads(raw)
            return [(i["headline"], i["url"], i.get("pub","Unknown"), bucket)
                    for i in items if i.get("headline") and i.get("url")]
    except Exception as e:
        log.warning(f"Perplexity error ({bucket}): {e}")
        return []

# ── Core fetch ────────────────────────────────────────────────────────────────
async def fetch_bucket(bucket):
    prune_old()
    rss_all    = await fetch_rss()
    rss_bucket = [(h,u,p,b) for h,u,p,b in rss_all if b == bucket]
    perp       = await fetch_perplexity(bucket)
    new, urgent = [], []
    for headline, url, pub, bkt in rss_bucket + perp:
        urg = is_urgent(headline)
        if add_article(headline, url, pub, bkt, urgent=urg):
            art = state["articles"][-1]
            new.append(art)
            if urg: urgent.append(art)
    return new, urgent

async def fetch_all():
    all_new, all_urgent = [], []
    for b in ["grab","comp","ind"]:
        n, u = await fetch_bucket(b)
        all_new.extend(n); all_urgent.extend(u)
    return all_new, all_urgent

# ── Formatting ────────────────────────────────────────────────────────────────
EMOJI = {"grab":"🟢","comp":"🟠","ind":"🔵"}

def fmt_sweep(new, label):
    today = datetime.now(SGT).strftime("%-d %B %Y")
    if not new: return f"*{label} sweep — {today}*\nNo new articles found."
    lines = [f"*{label} sweep — {today}*\n_{len(new)} new article(s)_\n"]
    for a in new:
        urg = "🚨 " if a["urgent"] else ""
        lines.append(f"{urg}{EMOJI.get(a['bucket'],'•')} [{a['headline']}]({a['url']}) — _{a['pub']}_")
    return "\n".join(lines)

def fmt_report():
    today_str   = datetime.now(SGT).strftime("%Y-%m-%d")
    today_label = datetime.now(SGT).strftime("%-d %B %Y")
    arts  = [a for a in state["articles"] if a["date"] == today_str]
    grab  = [a for a in arts if a["bucket"]=="grab"]
    comp  = [a for a in arts if a["bucket"]=="comp"]
    ind   = [a for a in arts if a["bucket"]=="ind"]
    lines = [
        f"📋 *The Grab Daily — {today_label}*",
        f"Subject: The Grab Daily - Daily Monitoring {today_label}\n",
        "Hi all,\n\nPlease find today's Grab Daily report below:\n",
    ]
    if grab:
        lines.append("*Grab*")
        for a in grab: lines.append(f"• [{a['headline']}]({a['url']}) _{a['pub']}_")
        lines.append("")
    if comp:
        lines.append("*Competitor News*")
        for a in comp: lines.append(f"• [{a['headline']}]({a['url']}) _{a['pub']}_")
        lines.append("")
    if ind:
        lines.append("*Industry News*")
        for a in ind: lines.append(f"• [{a['headline']}]({a['url']}) _{a['pub']}_")
        lines.append("")
    if not grab and not comp and not ind:
        lines.append("_No articles fetched yet today. Run /all first._")
    return "\n".join(lines)

# ── Commands ──────────────────────────────────────────────────────────────────
async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 *Grab Daily Bot*\n\n"
        "/grab — fetch Grab articles now\n"
        "/comp — fetch competitor articles now\n"
        "/industry — fetch industry articles now\n"
        "/all — fetch all three buckets now\n"
        "/report — draft today's Grab Daily report\n"
        "/watchlist — show active watchlist\n"
        "/watch keyword | notes | days — add watchlist item\n"
        "  e.g. /watch Grab AV Punggol | Expected follow-up | 7\n\n"
        "Auto-sweeps: 8am 10am 12pm 3pm 5pm 7pm SGT\n"
        "Report draft: sent to group at 12pm daily",
        parse_mode=ParseMode.MARKDOWN
    )

async def cmd_grab(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔍 Fetching Grab articles...")
    new, urgent = await fetch_bucket("grab")
    await ctx.bot.send_message(GROUP_CHAT_ID, fmt_sweep(new, "Grab"),
        parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True)
    for a in urgent:
        await ctx.bot.send_message(GROUP_CHAT_ID,
            f"🚨 *URGENT — Grab*\n[{a['headline']}]({a['url']})\n_{a['pub']}_",
            parse_mode=ParseMode.MARKDOWN)

async def cmd_comp(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔍 Fetching competitor articles...")
    new, _ = await fetch_bucket("comp")
    await ctx.bot.send_message(GROUP_CHAT_ID, fmt_sweep(new, "Competitors"),
        parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True)

async def cmd_industry(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔍 Fetching industry articles...")
    new, _ = await fetch_bucket("ind")
    await ctx.bot.send_message(GROUP_CHAT_ID, fmt_sweep(new, "Industry"),
        parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True)

async def cmd_all(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔍 Fetching all buckets...")
    new, urgent = await fetch_all()
    grab_n = sum(1 for a in new if a["bucket"]=="grab")
    comp_n = sum(1 for a in new if a["bucket"]=="comp")
    ind_n  = sum(1 for a in new if a["bucket"]=="ind")
    await ctx.bot.send_message(GROUP_CHAT_ID,
        f"🗞 *Full sweep — {datetime.now(SGT).strftime('%-d %B %Y %H:%M')} SGT*\n"
        f"🟢 Grab: {grab_n} new\n🟠 Competitors: {comp_n} new\n🔵 Industry: {ind_n} new",
        parse_mode=ParseMode.MARKDOWN)
    for a in new[:15]:
        urg = "🚨 " if a["urgent"] else ""
        await ctx.bot.send_message(GROUP_CHAT_ID,
            f"{urg}{EMOJI.get(a['bucket'],'•')} [{a['headline']}]({a['url']})\n_{a['pub']}_",
            parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True)
    for a in urgent:
        await ctx.bot.send_message(GROUP_CHAT_ID,
            f"🚨 *URGENT — {a['pub']}*\n[{a['headline']}]({a['url']})",
            parse_mode=ParseMode.MARKDOWN)

async def cmd_report(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await ctx.bot.send_message(GROUP_CHAT_ID, fmt_report(),
        parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True)

async def cmd_watchlist(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    now    = datetime.now(SGT)
    active = [w for w in state["watchlist"] if datetime.fromisoformat(w["due"]) >= now]
    if not active:
        await update.message.reply_text("📋 *Watchlist*\nNo active items.", parse_mode=ParseMode.MARKDOWN)
        return
    lines = ["📋 *Watchlist — active items*\n"]
    for w in active:
        due     = datetime.fromisoformat(w["due"])
        days    = (due - now).days
        matched = any(w["keyword"].lower().split()[0] in a["headline"].lower() for a in state["articles"])
        status  = "✅ Matched" if matched else f"👁 Watching ({days}d left)"
        lines.append(f"*{w['keyword']}* — {status}")
        if w.get("notes"): lines.append(f"  _{w['notes']}_")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)

async def cmd_watch(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text  = update.message.text.replace("/watch","").strip()
    parts = [p.strip() for p in text.split("|")]
    if not parts or not parts[0]:
        await update.message.reply_text("Usage: /watch keyword | notes | days\nExample: /watch Grab AV Punggol | Expected follow-up | 7")
        return
    keyword = parts[0]
    notes   = parts[1] if len(parts) > 1 else ""
    days    = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 7
    due     = (datetime.now(SGT) + timedelta(days=days)).isoformat()
    state["watchlist"].append({"keyword": keyword, "notes": notes, "due": due})
    save_state(state)
    due_label = (datetime.now(SGT) + timedelta(days=days)).strftime("%-d %b")
    await update.message.reply_text(f"✅ Watching: *{keyword}*\nUntil: {due_label}", parse_mode=ParseMode.MARKDOWN)

# ── Scheduled jobs ────────────────────────────────────────────────────────────
async def job_sweep(ctx: ContextTypes.DEFAULT_TYPE):
    log.info("Scheduled sweep running...")
    new, urgent = await fetch_all()
    if not new and not urgent: return
    grab_n = sum(1 for a in new if a["bucket"]=="grab")
    comp_n = sum(1 for a in new if a["bucket"]=="comp")
    ind_n  = sum(1 for a in new if a["bucket"]=="ind")
    now_str = datetime.now(SGT).strftime("%H:%M")
    await ctx.bot.send_message(GROUP_CHAT_ID,
        f"⏰ *{now_str} sweep*\n🟢 Grab: {grab_n}  🟠 Comp: {comp_n}  🔵 Industry: {ind_n} new",
        parse_mode=ParseMode.MARKDOWN)
    for a in new[:10]:
        urg = "🚨 " if a["urgent"] else ""
        await ctx.bot.send_message(GROUP_CHAT_ID,
            f"{urg}{EMOJI.get(a['bucket'],'•')} [{a['headline']}]({a['url']})\n_{a['pub']}_",
            parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True)
    for a in urgent:
        await ctx.bot.send_message(GROUP_CHAT_ID,
            f"🚨 *URGENT — {a['pub']}*\n[{a['headline']}]({a['url']})",
            parse_mode=ParseMode.MARKDOWN)

async def job_report(ctx: ContextTypes.DEFAULT_TYPE):
    log.info("Sending 12pm report...")
    await ctx.bot.send_message(GROUP_CHAT_ID, fmt_report(),
        parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True)
    today_str = datetime.now(SGT).strftime("%Y-%m-%d")
    matched = [w for w in state["watchlist"]
               if any(w["keyword"].lower().split()[0] in a["headline"].lower()
                      for a in state["articles"] if a["date"] == today_str)]
    if matched:
        kws = ", ".join(w["keyword"] for w in matched)
        await ctx.bot.send_message(GROUP_CHAT_ID,
            f"👁 *Watchlist matches today:* {kws}", parse_mode=ParseMode.MARKDOWN)

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

    # Schedules in SGT (UTC+8)
    jq = app.job_queue
    for hour in [8, 10, 12, 15, 17, 19]:
        jq.run_daily(job_sweep, time=time(hour=hour, minute=0, tzinfo=SGT))
    jq.run_daily(job_report, time=time(hour=12, minute=1, tzinfo=SGT))

    log.info("Bot started. Polling...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
