import logging, json, os, re
from datetime import datetime, timedelta
from telegram import Update, ChatPermissions, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    filters, ContextTypes, CallbackQueryHandler
)
from telegram.constants import ParseMode

logging.basicConfig(format="%(asctime)s | %(levelname)s | %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN  = os.environ.get("BOT_TOKEN", "")
OWNER_ID   = int(os.environ.get("OWNER_ID", "0"))
ADMIN_PASS = os.environ.get("ADMIN_PASS", "Aryan2010")
DATA_FILE  = "data.json"

authenticated_admins: set = set()
pending_auth: set = set()

# ─── Data ──────────────────────────────────────────────────────────────────────
def load() -> dict:
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE) as f:
            return json.load(f)
    return {"warns":{}, "rules":{}, "notes":{}, "filters":{},
            "blacklist":{}, "antiflood":{}, "flood_tracker":{}, "stats":{}}

def save(data: dict):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=2)

# ─── Helpers ───────────────────────────────────────────────────────────────────
def mention(user) -> str:
    name = (user.full_name or user.username or str(user.id))[:25]
    return f"<a href='tg://user?id={user.id}'>{name}</a>"

async def is_admin(update: Update, ctx: ContextTypes.DEFAULT_TYPE, uid: int = None) -> bool:
    u = uid or update.effective_user.id
    if u == OWNER_ID: return True
    try:
        m = await update.effective_chat.get_member(u)
        return m.status in ("administrator", "creator")
    except: return False

async def bot_ok(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> bool:
    m = await update.effective_chat.get_member(ctx.bot.id)
    return m.status in ("administrator", "creator")

async def resolve(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if msg.reply_to_message:
        return msg.reply_to_message.from_user, " ".join(ctx.args) if ctx.args else ""
    if ctx.args:
        arg = ctx.args[0].lstrip("@")
        reason = " ".join(ctx.args[1:])
        try:
            m = await update.effective_chat.get_member(int(arg)) if arg.isdigit() \
                else await update.effective_chat.get_member_by_username(arg)
            return m.user, reason
        except:
            await msg.reply_text("❌ User not found. Try replying to their message.")
            return None, None
    await msg.reply_text("❌ Reply to a message or provide @username.")
    return None, None

def parse_time(s: str):
    u = {"s": "seconds", "m": "minutes", "h": "hours", "d": "days"}
    if s and s[-1] in u:
        try: return timedelta(**{u[s[-1]]: int(s[:-1])})
        except: pass
    return None

def track_stat(data, cid, key):
    data["stats"].setdefault(str(cid), {})[key] = data["stats"].get(str(cid), {}).get(key, 0) + 1

# ─── Welcome / Leave ───────────────────────────────────────────────────────────
async def on_join(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    for m in update.message.new_chat_members:
        if m.is_bot: continue
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("📜 Rules", callback_data="show_rules"),
            InlineKeyboardButton("👮 Admins", callback_data="show_admins")
        ]])
        await update.message.reply_text(
            f"👋 Welcome to <b>{update.effective_chat.title}</b>, {mention(m)}!\n"
            f"Please read the rules. Have fun! 🎉",
            parse_mode=ParseMode.HTML, reply_markup=kb)

async def on_leave(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    m = update.message.left_chat_member
    if m and not m.is_bot:
        await update.message.reply_text(f"👋 {mention(m)} has left the group.", parse_mode=ParseMode.HTML)

# ─── Ban / Unban ───────────────────────────────────────────────────────────────
async def ban(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, ctx): return await update.message.reply_text("🚫 Admins only!")
    if not await bot_ok(update, ctx): return await update.message.reply_text("❌ Make me admin first!")
    user, reason = await resolve(update, ctx)
    if not user: return
    if await is_admin(update, ctx, user.id): return await update.message.reply_text("⚠️ Can't ban an admin!")
    await update.effective_chat.ban_member(user.id)
    data = load(); track_stat(data, update.effective_chat.id, "bans"); save(data)
    txt = f"🔨 <b>Banned</b> {mention(user)}"
    if reason: txt += f"\n📝 {reason}"
    await update.message.reply_text(txt, parse_mode=ParseMode.HTML)

async def unban(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, ctx): return await update.message.reply_text("🚫 Admins only!")
    user, _ = await resolve(update, ctx)
    if not user: return
    await update.effective_chat.unban_member(user.id)
    await update.message.reply_text(f"✅ {mention(user)} unbanned.", parse_mode=ParseMode.HTML)

# ─── Kick ──────────────────────────────────────────────────────────────────────
async def kick(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, ctx): return await update.message.reply_text("🚫 Admins only!")
    if not await bot_ok(update, ctx): return await update.message.reply_text("❌ Make me admin first!")
    user, reason = await resolve(update, ctx)
    if not user: return
    if await is_admin(update, ctx, user.id): return await update.message.reply_text("⚠️ Can't kick an admin!")
    await update.effective_chat.ban_member(user.id)
    await update.effective_chat.unban_member(user.id)
    data = load(); track_stat(data, update.effective_chat.id, "kicks"); save(data)
    txt = f"👢 <b>Kicked</b> {mention(user)}"
    if reason: txt += f"\n📝 {reason}"
    await update.message.reply_text(txt, parse_mode=ParseMode.HTML)

# ─── Mute / Unmute ─────────────────────────────────────────────────────────────
async def mute(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, ctx): return await update.message.reply_text("🚫 Admins only!")
    if not await bot_ok(update, ctx): return await update.message.reply_text("❌ Make me admin first!")
    user, reason = await resolve(update, ctx)
    if not user: return
    if await is_admin(update, ctx, user.id): return await update.message.reply_text("⚠️ Can't mute an admin!")
    args = ctx.args or []
    duration, time_str = None, ""
    for a in args:
        if re.match(r'^\d+[smhd]$', a):
            duration = parse_time(a); time_str = a; break
    until = datetime.now() + duration if duration else None
    await update.effective_chat.restrict_member(user.id, ChatPermissions(can_send_messages=False), until_date=until)
    data = load(); track_stat(data, update.effective_chat.id, "mutes"); save(data)
    txt = f"🔇 <b>Muted</b> {mention(user)}"
    if time_str: txt += f" for {time_str}"
    if reason: txt += f"\n📝 {reason}"
    await update.message.reply_text(txt, parse_mode=ParseMode.HTML)

async def unmute(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, ctx): return await update.message.reply_text("🚫 Admins only!")
    user, _ = await resolve(update, ctx)
    if not user: return
    await update.effective_chat.restrict_member(user.id, ChatPermissions(
        can_send_messages=True, can_send_media_messages=True, can_send_polls=True,
        can_send_other_messages=True, can_add_web_page_previews=True))
    await update.message.reply_text(f"🔊 {mention(user)} unmuted.", parse_mode=ParseMode.HTML)

# ─── Warn ──────────────────────────────────────────────────────────────────────
MAX_WARNS = 3

async def warn(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, ctx): return await update.message.reply_text("🚫 Admins only!")
    user, reason = await resolve(update, ctx)
    if not user: return
    if await is_admin(update, ctx, user.id): return await update.message.reply_text("⚠️ Can't warn an admin!")
    data = load()
    cid, uid = str(update.effective_chat.id), str(user.id)
    data["warns"].setdefault(cid, {}).setdefault(uid, [])
    data["warns"][cid][uid].append({"reason": reason, "time": datetime.now().isoformat()})
    count = len(data["warns"][cid][uid])
    track_stat(data, update.effective_chat.id, "warns"); save(data)
    txt = f"⚠️ <b>Warning {count}/{MAX_WARNS}</b> — {mention(user)}"
    if reason: txt += f"\n📝 {reason}"
    if count >= MAX_WARNS:
        await update.effective_chat.ban_member(user.id)
        data["warns"][cid][uid] = []; save(data)
        txt += f"\n\n🔨 <b>{MAX_WARNS} warnings reached — BANNED!</b>"
    await update.message.reply_text(txt, parse_mode=ParseMode.HTML)

async def unwarn(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, ctx): return await update.message.reply_text("🚫 Admins only!")
    user, _ = await resolve(update, ctx)
    if not user: return
    data = load()
    cid, uid = str(update.effective_chat.id), str(user.id)
    wlist = data["warns"].get(cid, {}).get(uid, [])
    if wlist:
        data["warns"][cid][uid].pop(); save(data)
        await update.message.reply_text(f"✅ 1 warning removed from {mention(user)} ({len(data['warns'][cid][uid])}/{MAX_WARNS})", parse_mode=ParseMode.HTML)
    else:
        await update.message.reply_text("ℹ️ No warnings found for this user.")

async def warns_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user, _ = await resolve(update, ctx)
    if not user: return
    data = load()
    wlist = data["warns"].get(str(update.effective_chat.id), {}).get(str(user.id), [])
    if not wlist:
        return await update.message.reply_text(f"✅ {mention(user)} has no warnings.", parse_mode=ParseMode.HTML)
    txt = f"⚠️ <b>{mention(user)} — {len(wlist)}/{MAX_WARNS} warnings:</b>\n"
    for i, w in enumerate(wlist, 1):
        txt += f"\n{i}. {w.get('reason') or 'No reason'}"
    await update.message.reply_text(txt, parse_mode=ParseMode.HTML)

# ─── Rules ─────────────────────────────────────────────────────────────────────
async def setrules(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, ctx): return await update.message.reply_text("🚫 Admins only!")
    if not ctx.args: return await update.message.reply_text("Usage: /setrules <text>")
    data = load(); data["rules"][str(update.effective_chat.id)] = " ".join(ctx.args); save(data)
    await update.message.reply_text("✅ Rules updated!")

async def rules(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    data = load()
    r = data["rules"].get(str(update.effective_chat.id))
    if not r: return await update.message.reply_text("📋 No rules set yet. Admin: /setrules <text>")
    await update.message.reply_text(f"📋 <b>{update.effective_chat.title} Rules:</b>\n\n{r}", parse_mode=ParseMode.HTML)

# ─── Blacklist ─────────────────────────────────────────────────────────────────
async def addbl(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, ctx): return await update.message.reply_text("🚫 Admins only!")
    if not ctx.args: return await update.message.reply_text("Usage: /addbl <word>")
    word = ctx.args[0].lower()
    data = load(); cid = str(update.effective_chat.id)
    data["blacklist"].setdefault(cid, [])
    if word not in data["blacklist"][cid]: data["blacklist"][cid].append(word); save(data)
    await update.message.reply_text(f"✅ `{word}` blacklisted.", parse_mode=ParseMode.MARKDOWN)

async def delbl(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, ctx): return await update.message.reply_text("🚫 Admins only!")
    if not ctx.args: return await update.message.reply_text("Usage: /delbl <word>")
    word = ctx.args[0].lower()
    data = load(); cid = str(update.effective_chat.id)
    if word in data["blacklist"].get(cid, []):
        data["blacklist"][cid].remove(word); save(data)
        await update.message.reply_text(f"🗑️ `{word}` removed.", parse_mode=ParseMode.MARKDOWN)
    else:
        await update.message.reply_text("❌ Word not in blacklist.")

async def blacklist_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    data = load()
    bl = data["blacklist"].get(str(update.effective_chat.id), [])
    if not bl: return await update.message.reply_text("🚫 Blacklist is empty.")
    await update.message.reply_text("🚫 <b>Blacklisted Words:</b>\n\n" + "\n".join(f"• <code>{w}</code>" for w in bl), parse_mode=ParseMode.HTML)

async def check_blacklist(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text: return
    if await is_admin(update, ctx): return
    data = load()
    bl = data["blacklist"].get(str(update.effective_chat.id), [])
    text = update.message.text.lower()
    for word in bl:
        if word in text:
            await update.message.delete()
            await update.effective_chat.send_message(
                f"⚠️ {mention(update.effective_user)}, that word is not allowed here!", parse_mode=ParseMode.HTML)
            return

# ─── Anti-flood ────────────────────────────────────────────────────────────────
async def setflood(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, ctx): return await update.message.reply_text("🚫 Admins only!")
    if not ctx.args or not ctx.args[0].isdigit(): return await update.message.reply_text("Usage: /setflood <n> (0=off)")
    limit = int(ctx.args[0])
    data = load(); data["antiflood"][str(update.effective_chat.id)] = limit; save(data)
    await update.message.reply_text(f"✅ Anti-flood {'disabled' if limit == 0 else f'set to {limit} msgs'}.")

async def check_flood(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not update.message or await is_admin(update, ctx): return
    data = load(); cid = str(update.effective_chat.id)
    limit = data["antiflood"].get(cid, 0)
    if limit == 0: return
    uid = str(update.effective_user.id); now = datetime.now().timestamp()
    t = data["flood_tracker"].setdefault(cid, {}).setdefault(uid, {"count": 0, "reset": now + 10})
    if now > t["reset"]: t["count"] = 1; t["reset"] = now + 10
    else: t["count"] += 1
    if t["count"] >= limit:
        t["count"] = 0
        await update.effective_chat.restrict_member(
            update.effective_user.id, ChatPermissions(can_send_messages=False),
            until_date=datetime.now() + timedelta(minutes=5))
        await update.effective_chat.send_message(
            f"🌊 {mention(update.effective_user)} was muted 5 minutes — flooding!", parse_mode=ParseMode.HTML)
    save(data)

# ─── Notes ─────────────────────────────────────────────────────────────────────
async def save_note(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, ctx): return await update.message.reply_text("🚫 Admins only!")
    if len(ctx.args) < 2: return await update.message.reply_text("Usage: /save <name> <content>")
    name, content = ctx.args[0].lower(), " ".join(ctx.args[1:])
    data = load(); data["notes"].setdefault(str(update.effective_chat.id), {})[name] = content; save(data)
    await update.message.reply_text(f"📝 Note `{name}` saved!", parse_mode=ParseMode.MARKDOWN)

async def get_note(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args: return await update.message.reply_text("Usage: /get <name>")
    name = ctx.args[0].lower()
    data = load(); note = data["notes"].get(str(update.effective_chat.id), {}).get(name)
    if not note: return await update.message.reply_text(f"❌ Note `{name}` not found.", parse_mode=ParseMode.MARKDOWN)
    await update.message.reply_text(f"📝 <b>{name}:</b>\n\n{note}", parse_mode=ParseMode.HTML)

async def notes_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    data = load(); notes = data["notes"].get(str(update.effective_chat.id), {})
    if not notes: return await update.message.reply_text("📝 No notes saved yet.")
    await update.message.reply_text("📝 <b>Saved Notes:</b>\n\n" + "\n".join(f"• <code>{n}</code>" for n in notes), parse_mode=ParseMode.HTML)

async def delnote(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, ctx): return await update.message.reply_text("🚫 Admins only!")
    if not ctx.args: return await update.message.reply_text("Usage: /delnote <name>")
    name = ctx.args[0].lower(); data = load(); cid = str(update.effective_chat.id)
    if name in data["notes"].get(cid, {}):
        del data["notes"][cid][name]; save(data)
        await update.message.reply_text(f"🗑️ Note `{name}` deleted!", parse_mode=ParseMode.MARKDOWN)
    else: await update.message.reply_text("❌ Note not found.")

# ─── Filters ───────────────────────────────────────────────────────────────────
async def add_filter(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, ctx): return await update.message.reply_text("🚫 Admins only!")
    if len(ctx.args) < 2: return await update.message.reply_text("Usage: /filter <keyword> <reply>")
    kw, reply = ctx.args[0].lower(), " ".join(ctx.args[1:])
    data = load(); data["filters"].setdefault(str(update.effective_chat.id), {})[kw] = reply; save(data)
    await update.message.reply_text(f"✅ Filter `{kw}` added!", parse_mode=ParseMode.MARKDOWN)

async def del_filter(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, ctx): return await update.message.reply_text("🚫 Admins only!")
    if not ctx.args: return await update.message.reply_text("Usage: /delfilter <keyword>")
    kw = ctx.args[0].lower(); data = load(); cid = str(update.effective_chat.id)
    if kw in data["filters"].get(cid, {}):
        del data["filters"][cid][kw]; save(data)
        await update.message.reply_text(f"🗑️ Filter `{kw}` removed.", parse_mode=ParseMode.MARKDOWN)
    else: await update.message.reply_text("❌ Filter not found.")

async def filters_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    data = load(); f = data["filters"].get(str(update.effective_chat.id), {})
    if not f: return await update.message.reply_text("🔍 No filters active.")
    await update.message.reply_text("🔍 <b>Active Filters:</b>\n\n" + "\n".join(f"• <code>{k}</code>" for k in f), parse_mode=ParseMode.HTML)

async def check_filters(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text: return
    data = load(); f = data["filters"].get(str(update.effective_chat.id), {})
    text = update.message.text.lower()
    for kw, reply in f.items():
        if kw in text: await update.message.reply_text(reply); break

# ─── Promote / Demote / Pin / Del ──────────────────────────────────────────────
async def promote(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, ctx): return await update.message.reply_text("🚫 Admins only!")
    if not await bot_ok(update, ctx): return await update.message.reply_text("❌ Make me admin first!")
    user, _ = await resolve(update, ctx)
    if not user: return
    await update.effective_chat.promote_member(user.id, can_change_info=True, can_delete_messages=True,
        can_invite_users=True, can_restrict_members=True, can_pin_messages=True)
    await update.message.reply_text(f"⭐ {mention(user)} promoted to admin!", parse_mode=ParseMode.HTML)

async def demote(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, ctx): return await update.message.reply_text("🚫 Admins only!")
    if not await bot_ok(update, ctx): return await update.message.reply_text("❌ Make me admin first!")
    user, _ = await resolve(update, ctx)
    if not user: return
    await update.effective_chat.promote_member(user.id, can_change_info=False, can_delete_messages=False,
        can_invite_users=False, can_restrict_members=False, can_pin_messages=False)
    await update.message.reply_text(f"⬇️ {mention(user)} demoted.", parse_mode=ParseMode.HTML)

async def pin(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, ctx): return await update.message.reply_text("🚫 Admins only!")
    if not update.message.reply_to_message: return await update.message.reply_text("📌 Reply to a message to pin it.")
    await update.message.reply_to_message.pin()
    await update.message.reply_text("📌 Message pinned!")

async def unpin(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, ctx): return await update.message.reply_text("🚫 Admins only!")
    await update.effective_chat.unpin_message()
    await update.message.reply_text("📌 Message unpinned!")

async def delete_msg(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, ctx): return await update.message.reply_text("🚫 Admins only!")
    if not update.message.reply_to_message: return await update.message.reply_text("🗑️ Reply to a message to delete it.")
    await update.message.reply_to_message.delete()
    await update.message.delete()

# ─── Info / Stats / Broadcast ──────────────────────────────────────────────────
async def adminlist(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    admins = await update.effective_chat.get_administrators()
    txt = f"👑 <b>{update.effective_chat.title} — Admins:</b>\n\n"
    for a in admins:
        if a.user.is_bot: continue
        title = a.custom_title or ("Owner" if a.status == "creator" else "Admin")
        txt += f"• {mention(a.user)} — <i>{title}</i>\n"
    await update.message.reply_text(txt, parse_mode=ParseMode.HTML)

async def chatinfo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat; count = await chat.get_member_count()
    await update.message.reply_text(
        f"ℹ️ <b>Group Info</b>\n\n📛 <b>{chat.title}</b>\n🆔 <code>{chat.id}</code>\n"
        f"👥 Members: <b>{count}</b>\n🔗 @{chat.username or 'Private'}", parse_mode=ParseMode.HTML)

async def userinfo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.message.reply_to_message.from_user if update.message.reply_to_message else update.effective_user
    await update.message.reply_text(
        f"👤 <b>User Info</b>\n\n📛 <b>{user.full_name}</b>\n🆔 <code>{user.id}</code>\n"
        f"🔖 @{user.username or 'N/A'}\n🤖 Bot: {'Yes' if user.is_bot else 'No'}", parse_mode=ParseMode.HTML)

async def stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, ctx): return await update.message.reply_text("🚫 Admins only!")
    data = load(); s = data["stats"].get(str(update.effective_chat.id), {})
    await update.message.reply_text(
        f"📊 <b>Group Stats</b>\n\n🔨 Bans: <b>{s.get('bans',0)}</b>\n👢 Kicks: <b>{s.get('kicks',0)}</b>\n"
        f"🔇 Mutes: <b>{s.get('mutes',0)}</b>\n⚠️ Warns: <b>{s.get('warns',0)}</b>", parse_mode=ParseMode.HTML)

async def get_id(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.message.reply_to_message:
        u = update.message.reply_to_message.from_user
        await update.message.reply_text(f"👤 {mention(u)}\n🆔 <code>{u.id}</code>", parse_mode=ParseMode.HTML)
    else:
        await update.message.reply_text(
            f"🆔 Your ID: <code>{update.effective_user.id}</code>\n💬 Chat ID: <code>{update.effective_chat.id}</code>",
            parse_mode=ParseMode.HTML)

async def broadcast(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return await update.message.reply_text("🚫 Owner only!")
    if not ctx.args: return await update.message.reply_text("Usage: /broadcast <message>")
    await update.effective_chat.send_message(f"📢 <b>Announcement:</b>\n\n{' '.join(ctx.args)}", parse_mode=ParseMode.HTML)

# ─── Admin Panel (Private Chat) ────────────────────────────────────────────────
async def admin_panel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        return await update.message.reply_text("📱 Send /admin to me in private chat!")
    uid = update.effective_user.id
    if uid in authenticated_admins:
        await show_panel(update, ctx)
    else:
        pending_auth.add(uid)
        await update.message.reply_text("🔐 <b>Admin Panel</b>\n\nEnter the admin password:", parse_mode=ParseMode.HTML)

async def show_panel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    data = load()
    total = {k: sum(v.get(k, 0) for v in data["stats"].values()) for k in ["bans","kicks","mutes","warns"]}
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 Stats", callback_data="ap_stats"),
         InlineKeyboardButton("📋 Commands", callback_data="ap_help")],
        [InlineKeyboardButton("🚪 Logout", callback_data="ap_logout")]
    ])
    await update.effective_message.reply_text(
        f"🛡️ <b>Mahakaal Admin Panel</b>\n\n"
        f"✅ Authenticated!\n\n"
        f"📊 <b>Quick Stats:</b>\n"
        f"🔨 Bans: {total['bans']} | 👢 Kicks: {total['kicks']}\n"
        f"🔇 Mutes: {total['mutes']} | ⚠️ Warns: {total['warns']}\n\n"
        f"<i>Use commands in your groups. Session active until restart.</i>",
        parse_mode=ParseMode.HTML, reply_markup=kb)

async def handle_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text: return
    uid = update.effective_user.id
    chat_type = update.effective_chat.type

    # Private chat — password auth
    if chat_type == "private" and uid in pending_auth:
        pending_auth.discard(uid)
        if update.message.text.strip() == ADMIN_PASS:
            authenticated_admins.add(uid)
            await show_panel(update, ctx)
        else:
            await update.message.reply_text("❌ Wrong password! Try /admin again.")
        return

    # Group — filters, blacklist, flood
    if chat_type in ("group", "supergroup"):
        await check_blacklist(update, ctx)
        await check_filters(update, ctx)
        await check_flood(update, ctx)

async def panel_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    uid = update.effective_user.id

    if q.data == "ap_logout":
        authenticated_admins.discard(uid)
        await q.message.reply_text("🚪 Logged out.")

    elif q.data == "ap_stats":
        data = load()
        total = {k: sum(v.get(k,0) for v in data["stats"].values()) for k in ["bans","kicks","mutes","warns"]}
        await q.message.reply_text(
            f"📊 <b>Global Stats</b>\n\n🔨 Bans: <b>{total['bans']}</b>\n👢 Kicks: <b>{total['kicks']}</b>\n"
            f"🔇 Mutes: <b>{total['mutes']}</b>\n⚠️ Warns: <b>{total['warns']}</b>\n📋 Groups: <b>{len(data['stats'])}</b>",
            parse_mode=ParseMode.HTML)

    elif q.data == "ap_help":
        await q.message.reply_text(
            "📋 <b>All Commands:</b>\n\n"
            "<b>Mod:</b> /ban /unban /kick /mute /unmute /warn /unwarn /warns\n"
            "<b>Mgmt:</b> /promote /demote /pin /unpin /del\n"
            "<b>Auto:</b> /addbl /delbl /blacklist /setflood /filter /delfilter /filters\n"
            "<b>Notes:</b> /save /get /notes /delnote\n"
            "<b>Info:</b> /adminlist /chatinfo /userinfo /id /stats\n"
            "<b>Rules:</b> /setrules /rules\n"
            "<b>Owner:</b> /broadcast",
            parse_mode=ParseMode.HTML)

    elif q.data == "show_rules":
        data = load(); r = data["rules"].get(str(update.effective_chat.id), "No rules set yet.")
        await q.message.reply_text(f"📋 <b>Rules:</b>\n\n{r}", parse_mode=ParseMode.HTML)

    elif q.data == "show_admins":
        try:
            admins = await update.effective_chat.get_administrators()
            txt = "👑 <b>Admins:</b>\n\n"
            for a in admins:
                if not a.user.is_bot: txt += f"• {mention(a.user)}\n"
            await q.message.reply_text(txt, parse_mode=ParseMode.HTML)
        except: await q.message.reply_text("❌ Can't fetch admins here.")

# ─── Start / Help ──────────────────────────────────────────────────────────────
HELP = (
    "🤖 <b>Mahakaal Group Bot — Commands</b>\n\n"
    "<b>🛡️ Moderation:</b>\n"
    "/ban · /unban · /kick\n"
    "/mute [10m/2h/1d] · /unmute\n"
    "/warn · /unwarn · /warns\n\n"
    "<b>⚙️ Management:</b>\n"
    "/promote · /demote · /pin · /unpin · /del\n"
    "/setrules · /rules\n\n"
    "<b>🔍 Auto-Tools:</b>\n"
    "/addbl · /delbl · /blacklist\n"
    "/setflood · /filter · /delfilter · /filters\n\n"
    "<b>📝 Notes:</b> /save · /get · /notes · /delnote\n\n"
    "<b>ℹ️ Info:</b> /adminlist · /chatinfo · /userinfo · /id · /stats\n\n"
    "<b>📢 Owner:</b> /broadcast\n\n"
    "<b>🔐 Admin Panel:</b> /admin (private chat only)\n\n"
    "<i>💡 Reply to a user's message + command = easy targeting!</i>"
)

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type == "private":
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔐 Admin Panel", callback_data="go_admin"),
                                     InlineKeyboardButton("📋 Help", callback_data="ap_help")]])
        await update.message.reply_text(
            "👋 <b>Mahakaal Group Bot</b>\n\nAdd me to your group and make me Admin.\n"
            "Use /admin to access the admin panel.",
            parse_mode=ParseMode.HTML, reply_markup=kb)
    else:
        await update.message.reply_text(HELP, parse_mode=ParseMode.HTML)

async def help_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(HELP, parse_mode=ParseMode.HTML)

async def go_admin_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    await admin_panel(update, ctx)

# ─── Main ──────────────────────────────────────────────────────────────────────
def main():
    if not BOT_TOKEN:
        logger.error("BOT_TOKEN not set!"); return

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, on_join))
    app.add_handler(MessageHandler(filters.StatusUpdate.LEFT_CHAT_MEMBER, on_leave))

    for cmd, fn in [
        ("start", start), ("help", help_cmd), ("admin", admin_panel),
        ("ban", ban), ("unban", unban), ("kick", kick),
        ("mute", mute), ("unmute", unmute),
        ("warn", warn), ("unwarn", unwarn), ("warns", warns_cmd),
        ("promote", promote), ("demote", demote),
        ("pin", pin), ("unpin", unpin), ("del", delete_msg),
        ("setrules", setrules), ("rules", rules),
        ("addbl", addbl), ("delbl", delbl), ("blacklist", blacklist_cmd),
        ("setflood", setflood),
        ("save", save_note), ("get", get_note), ("notes", notes_cmd), ("delnote", delnote),
        ("filter", add_filter), ("delfilter", del_filter), ("filters", filters_cmd),
        ("adminlist", adminlist), ("chatinfo", chatinfo), ("userinfo", userinfo),
        ("id", get_id), ("stats", stats), ("broadcast", broadcast),
    ]:
        app.add_handler(CommandHandler(cmd, fn))

    app.add_handler(CallbackQueryHandler(panel_cb, pattern="^(ap_|show_rules|show_admins)"))
    app.add_handler(CallbackQueryHandler(go_admin_cb, pattern="^go_admin$"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    logger.info("🚀 Mahakaal Group Bot running!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
