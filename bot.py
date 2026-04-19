import logging, json, os, re, asyncio
from datetime import datetime, timedelta
from telegram import Update, ChatPermissions, InlineKeyboardButton, InlineKeyboardMarkup, Bot
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    filters, ContextTypes, CallbackQueryHandler
)
from telegram.constants import ParseMode

logging.basicConfig(format="%(asctime)s | %(levelname)s | %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
OWNER_ID  = int(os.environ.get("OWNER_ID", "0"))
DATA_FILE = "data.json"

# ─── Data ──────────────────────────────────────────────────────────────────────
def load() -> dict:
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE) as f:
                return json.load(f)
        except:
            pass
    return {
        "warns": {}, "rules": {}, "notes": {}, "filters": {},
        "blacklist": {}, "antiflood": {}, "flood_tracker": {},
        "stats": {}, "groups": {}, "msg_count": {}, "welcome": {},
        "locks": {}
    }

def save(data: dict):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=2)

def ensure_keys(data):
    for key in ["warns","rules","notes","filters","blacklist","antiflood",
                "flood_tracker","stats","groups","msg_count","welcome","locks"]:
        if key not in data:
            data[key] = {}
    return data

# ─── Helpers ───────────────────────────────────────────────────────────────────
def mention(user) -> str:
    name = (user.full_name or user.username or str(user.id))[:25]
    return f"<a href='tg://user?id={user.id}'>{name}</a>"

async def get_chat_member_safe(chat, uid):
    try:
        return await chat.get_member(uid)
    except:
        return None

async def is_admin(update: Update, ctx: ContextTypes.DEFAULT_TYPE, uid: int = None) -> bool:
    u = uid or update.effective_user.id
    if u == OWNER_ID:
        return True
    try:
        m = await update.effective_chat.get_member(u)
        return m.status in ("administrator", "creator")
    except:
        return False

async def is_group_owner(update: Update, uid: int = None) -> bool:
    """Check if user is the creator/owner of this group"""
    u = uid or update.effective_user.id
    try:
        m = await update.effective_chat.get_member(u)
        return m.status == "creator"
    except:
        return False

async def bot_ok(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> bool:
    try:
        m = await update.effective_chat.get_member(ctx.bot.id)
        return m.status in ("administrator", "creator")
    except:
        return False

async def resolve(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if msg.reply_to_message:
        return msg.reply_to_message.from_user, " ".join(ctx.args) if ctx.args else ""
    if ctx.args:
        arg = ctx.args[0].lstrip("@")
        reason = " ".join(ctx.args[1:])
        try:
            if arg.isdigit():
                m = await update.effective_chat.get_member(int(arg))
            else:
                m = await update.effective_chat.get_member_by_username(arg)
            return m.user, reason
        except:
            await msg.reply_text("❌ User not found. Try replying to their message.")
            return None, None
    await msg.reply_text("❌ Reply to a message or provide @username.")
    return None, None

def parse_time(s: str):
    u = {"s": "seconds", "m": "minutes", "h": "hours", "d": "days"}
    if s and s[-1] in u:
        try:
            return timedelta(**{u[s[-1]]: int(s[:-1])})
        except:
            pass
    return None

def track_stat(data, cid, key):
    data["stats"].setdefault(str(cid), {})[key] = data["stats"].get(str(cid), {}).get(key, 0) + 1

async def register_group(ctx: ContextTypes.DEFAULT_TYPE, chat, owner_id=None):
    """Track groups where bot is added"""
    data = load()
    data = ensure_keys(data)
    cid = str(chat.id)
    if cid not in data["groups"]:
        data["groups"][cid] = {
            "title": chat.title,
            "id": chat.id,
            "username": chat.username or "",
            "owner_id": owner_id,
            "added": datetime.now().isoformat()
        }
    else:
        data["groups"][cid]["title"] = chat.title
        if owner_id:
            data["groups"][cid]["owner_id"] = owner_id
    save(data)

# ─── START / PRIVATE GREETING ──────────────────────────────────────────────────
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat = update.effective_chat

    if chat.type == "private":
        # Check deep link args (for group panel)
        if ctx.args and ctx.args[0].startswith("panel_"):
            cid = ctx.args[0].replace("panel_", "")
            await show_group_panel(update, ctx, cid)
            return

        kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("➕ Add Me to Group", url=f"https://t.me/{ctx.bot.username}?startgroup=true"),
                InlineKeyboardButton("📋 Help", callback_data="show_help")
            ],
            [
                InlineKeyboardButton("ℹ️ About", callback_data="show_about"),
                InlineKeyboardButton("👑 My Panel", callback_data="show_my_panel")
            ]
        ])
        await update.message.reply_text(
            f"👋 <b>Hello, {mention(user)}!</b>\n\n"
            f"🤖 <b>I'm your powerful Group Manager Bot!</b>\n\n"
            f"<b>What I can do:</b>\n"
            f"🔨 Ban / Kick / Mute / Warn members\n"
            f"📌 Pin & manage messages\n"
            f"🚫 Auto-delete blacklisted words\n"
            f"⚠️ Anti-flood protection\n"
            f"📢 Broadcast announcements\n"
            f"📊 View group statistics\n"
            f"🚨 Report system for users\n"
            f"🔒 Lock / unlock group permissions\n\n"
            f"👇 <b>Add me to your group and make me an Admin!</b>",
            parse_mode=ParseMode.HTML, reply_markup=kb
        )
    else:
        await update.message.reply_text(
            f"👋 {mention(user)}, I'm active and ready!\n"
            f"Type /help to see all commands. 🤖",
            parse_mode=ParseMode.HTML
        )

# ─── Welcome / Leave ───────────────────────────────────────────────────────────
async def on_join(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    data = load()
    data = ensure_keys(data)
    cid = str(update.effective_chat.id)

    for m in update.message.new_chat_members:
        if m.id == ctx.bot.id:
            # Bot was added to group — register it
            try:
                admins = await update.effective_chat.get_administrators()
                owner = next((a for a in admins if a.status == "creator"), None)
                owner_id = owner.user.id if owner else None
            except:
                owner_id = None
            await register_group(ctx, update.effective_chat, owner_id)
            await update.message.reply_text(
                f"👋 <b>Hello everyone!</b> I'm your new Group Manager Bot!\n\n"
                f"✅ <b>Make me an Admin and I'll handle:</b>\n"
                f"• Banning / kicking / muting users\n"
                f"• Deleting unwanted messages\n"
                f"• Anti-flood & blacklist protection\n"
                f"• Managing group permissions automatically\n\n"
                f"👑 <b>Group owner: send /panel in DM to access your control panel!</b>",
                parse_mode=ParseMode.HTML
            )
            return
        if m.is_bot:
            continue

        # Custom welcome or default
        welcome_msg = data["welcome"].get(cid, {}).get("msg", "")
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("📜 Rules", callback_data=f"show_rules_{cid}"),
            InlineKeyboardButton("👮 Admins", callback_data=f"show_admins_{cid}")
        ]])
        if welcome_msg:
            text = welcome_msg.replace("{name}", mention(m)).replace("{group}", update.effective_chat.title or "")
        else:
            text = (
                f"👋 <b>Welcome to {update.effective_chat.title}!</b>\n"
                f"Glad to have you here, {mention(m)}! 🎉\n\n"
                f"📋 Please read the rules before chatting!"
            )
        await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)

        # Track msg count
        data["msg_count"].setdefault(cid, {})
        save(data)

async def on_leave(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    m = update.message.left_chat_member
    if m and not m.is_bot:
        await update.message.reply_text(
            f"😢 {mention(m)} has left the group. Goodbye!",
            parse_mode=ParseMode.HTML
        )

async def setwelcome(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, ctx):
        return await update.message.reply_text("🚫 Only admins can set the welcome message!")
    if not ctx.args:
        return await update.message.reply_text(
            "📝 <b>Usage:</b> /setwelcome &lt;message&gt;\n"
            "✨ <b>Variables:</b> {name} = user mention, {group} = group name",
            parse_mode=ParseMode.HTML
        )
    data = load()
    data = ensure_keys(data)
    cid = str(update.effective_chat.id)
    data["welcome"].setdefault(cid, {})["msg"] = " ".join(ctx.args)
    save(data)
    await update.message.reply_text("✅ Welcome message has been set successfully!")

# ─── BAN / UNBAN ───────────────────────────────────────────────────────────────
async def ban(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, ctx):
        return await update.message.reply_text("🚫 Only admins can ban users!")
    if not await bot_ok(update, ctx):
        return await update.message.reply_text("❌ Make me an admin first!")

    user, reason = await resolve(update, ctx)
    if not user:
        return

    # Can't ban bot owner
    if user.id == OWNER_ID:
        return await update.message.reply_text("🛡️ The bot owner cannot be banned!")

    # Can't ban group owner/creator
    try:
        target_member = await update.effective_chat.get_member(user.id)
        if target_member.status == "creator":
            return await update.message.reply_text("🛡️ The group owner cannot be banned!")
    except:
        pass

    # Admins can only be banned by group owner or bot owner
    if await is_admin(update, ctx, user.id):
        if not (await is_group_owner(update) or update.effective_user.id == OWNER_ID):
            return await update.message.reply_text("⚠️ Only the group owner can ban an admin!")

    await update.effective_chat.ban_member(user.id)
    data = load()
    data = ensure_keys(data)
    track_stat(data, update.effective_chat.id, "bans")
    save(data)

    txt = f"🔨 <b>User Banned!</b>\n👤 {mention(user)} has been banned"
    if reason:
        txt += f"\n📝 <b>Reason:</b> {reason}"
    txt += f"\n👮 <b>By:</b> {mention(update.effective_user)}"
    await update.message.reply_text(txt, parse_mode=ParseMode.HTML)

async def unban(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, ctx):
        return await update.message.reply_text("🚫 Only admins can unban users!")
    user, _ = await resolve(update, ctx)
    if not user:
        return
    await update.effective_chat.unban_member(user.id)
    await update.message.reply_text(
        f"✅ {mention(user)} has been unbanned and can rejoin!",
        parse_mode=ParseMode.HTML
    )

async def banlist(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, ctx):
        return await update.message.reply_text("🚫 Only admins can view this!")
    data = load()
    data = ensure_keys(data)
    cid = str(update.effective_chat.id)
    bans  = data["stats"].get(cid, {}).get("bans", 0)
    kicks = data["stats"].get(cid, {}).get("kicks", 0)
    mutes = data["stats"].get(cid, {}).get("mutes", 0)
    warns = data["stats"].get(cid, {}).get("warns", 0)
    await update.message.reply_text(
        f"📊 <b>Group Action Stats</b>\n\n"
        f"🔨 Total Bans: <b>{bans}</b>\n"
        f"👢 Total Kicks: <b>{kicks}</b>\n"
        f"🔇 Total Mutes: <b>{mutes}</b>\n"
        f"⚠️ Total Warns: <b>{warns}</b>",
        parse_mode=ParseMode.HTML
    )

# ─── KICK ──────────────────────────────────────────────────────────────────────
async def kick(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, ctx):
        return await update.message.reply_text("🚫 Only admins can kick users!")
    if not await bot_ok(update, ctx):
        return await update.message.reply_text("❌ Make me an admin first!")
    user, reason = await resolve(update, ctx)
    if not user:
        return
    if user.id == OWNER_ID:
        return await update.message.reply_text("🛡️ The bot owner cannot be kicked!")
    try:
        target = await update.effective_chat.get_member(user.id)
        if target.status == "creator":
            return await update.message.reply_text("🛡️ The group owner cannot be kicked!")
    except:
        pass
    if await is_admin(update, ctx, user.id):
        if not (await is_group_owner(update) or update.effective_user.id == OWNER_ID):
            return await update.message.reply_text("⚠️ Only the group owner can kick an admin!")
    await update.effective_chat.ban_member(user.id)
    await update.effective_chat.unban_member(user.id)
    data = load()
    data = ensure_keys(data)
    track_stat(data, update.effective_chat.id, "kicks")
    save(data)
    txt = f"👢 <b>User Kicked!</b>\n👤 {mention(user)} has been kicked from the group"
    if reason:
        txt += f"\n📝 <b>Reason:</b> {reason}"
    txt += f"\n👮 <b>By:</b> {mention(update.effective_user)}"
    await update.message.reply_text(txt, parse_mode=ParseMode.HTML)

# ─── MUTE / UNMUTE ─────────────────────────────────────────────────────────────
async def mute(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, ctx):
        return await update.message.reply_text("🚫 Only admins can mute users!")
    if not await bot_ok(update, ctx):
        return await update.message.reply_text("❌ Make me an admin first!")
    user, reason = await resolve(update, ctx)
    if not user:
        return
    if user.id == OWNER_ID:
        return await update.message.reply_text("🛡️ The bot owner cannot be muted!")
    try:
        target = await update.effective_chat.get_member(user.id)
        if target.status == "creator":
            return await update.message.reply_text("🛡️ The group owner cannot be muted!")
    except:
        pass
    if await is_admin(update, ctx, user.id):
        if not (await is_group_owner(update) or update.effective_user.id == OWNER_ID):
            return await update.message.reply_text("⚠️ Only the group owner can mute an admin!")
    args = ctx.args or []
    duration, time_str = None, ""
    for a in args:
        if re.match(r'^\d+[smhd]$', a):
            duration = parse_time(a)
            time_str = a
            break
    until = datetime.now() + duration if duration else None
    await update.effective_chat.restrict_member(
        user.id, ChatPermissions(can_send_messages=False), until_date=until
    )
    data = load()
    data = ensure_keys(data)
    track_stat(data, update.effective_chat.id, "mutes")
    save(data)
    txt = f"🔇 <b>User Muted!</b>\n👤 {mention(user)}"
    if time_str:
        txt += f" — <b>for {time_str}</b>"
    else:
        txt += " — <b>Permanently</b>"
    if reason:
        txt += f"\n📝 <b>Reason:</b> {reason}"
    txt += f"\n👮 <b>By:</b> {mention(update.effective_user)}"
    await update.message.reply_text(txt, parse_mode=ParseMode.HTML)

async def unmute(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, ctx):
        return await update.message.reply_text("🚫 Only admins can unmute users!")
    if not await bot_ok(update, ctx):
        return await update.message.reply_text("❌ Make me an admin first!")
    user, _ = await resolve(update, ctx)
    if not user:
        return
    try:
        # FIX: Restore full default permissions
        await update.effective_chat.restrict_member(
            user.id,
            ChatPermissions(
                can_send_messages=True,
                can_send_audios=True,
                can_send_documents=True,
                can_send_photos=True,
                can_send_videos=True,
                can_send_video_notes=True,
                can_send_voice_notes=True,
                can_send_polls=True,
                can_send_other_messages=True,
                can_add_web_page_previews=True,
                can_change_info=False,
                can_invite_users=True,
                can_pin_messages=False,
            )
        )
        await update.message.reply_text(
            f"🔊 <b>User Unmuted!</b>\n👤 {mention(user)} can now send messages again.\n"
            f"👮 <b>By:</b> {mention(update.effective_user)}",
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        await update.message.reply_text(f"❌ Failed to unmute: {e}")

# ─── WARN ──────────────────────────────────────────────────────────────────────
MAX_WARNS = 3

async def warn(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, ctx):
        return await update.message.reply_text("🚫 Only admins can warn users!")
    user, reason = await resolve(update, ctx)
    if not user:
        return
    if user.id == OWNER_ID:
        return await update.message.reply_text("🛡️ The bot owner cannot be warned!")
    try:
        target = await update.effective_chat.get_member(user.id)
        if target.status == "creator":
            return await update.message.reply_text("🛡️ The group owner cannot be warned!")
    except:
        pass
    if await is_admin(update, ctx, user.id):
        return await update.message.reply_text("⚠️ Admins cannot be warned!")
    data = load()
    data = ensure_keys(data)
    cid, uid = str(update.effective_chat.id), str(user.id)
    data["warns"].setdefault(cid, {}).setdefault(uid, [])
    data["warns"][cid][uid].append({"reason": reason, "time": datetime.now().isoformat()})
    count = len(data["warns"][cid][uid])
    track_stat(data, update.effective_chat.id, "warns")
    save(data)
    txt = f"⚠️ <b>Warning {count}/{MAX_WARNS}</b>\n👤 {mention(user)}"
    if reason:
        txt += f"\n📝 <b>Reason:</b> {reason}"
    txt += f"\n👮 <b>By:</b> {mention(update.effective_user)}"
    if count >= MAX_WARNS:
        await update.effective_chat.ban_member(user.id)
        data["warns"][cid][uid] = []
        save(data)
        txt += f"\n\n🔨 <b>{MAX_WARNS} warnings reached → AUTO BANNED!</b>"
    await update.message.reply_text(txt, parse_mode=ParseMode.HTML)

async def unwarn(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, ctx):
        return await update.message.reply_text("🚫 Only admins can remove warnings!")
    user, _ = await resolve(update, ctx)
    if not user:
        return
    data = load()
    data = ensure_keys(data)
    cid, uid = str(update.effective_chat.id), str(user.id)
    wlist = data["warns"].get(cid, {}).get(uid, [])
    if wlist:
        data["warns"][cid][uid].pop()
        save(data)
        remaining = len(data["warns"][cid][uid])
        await update.message.reply_text(
            f"✅ Removed 1 warning from {mention(user)}. ({remaining}/{MAX_WARNS} remaining)",
            parse_mode=ParseMode.HTML
        )
    else:
        await update.message.reply_text(f"ℹ️ {mention(user)} has no warnings.", parse_mode=ParseMode.HTML)

async def warns_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user, _ = await resolve(update, ctx)
    if not user:
        return
    data = load()
    data = ensure_keys(data)
    wlist = data["warns"].get(str(update.effective_chat.id), {}).get(str(user.id), [])
    if not wlist:
        return await update.message.reply_text(
            f"✅ {mention(user)} has no warnings.", parse_mode=ParseMode.HTML
        )
    txt = f"⚠️ <b>{mention(user)} — {len(wlist)}/{MAX_WARNS} warnings:</b>\n"
    for i, w in enumerate(wlist, 1):
        txt += f"\n{i}. {w.get('reason') or 'No reason given'} <i>({w.get('time','')[:10]})</i>"
    await update.message.reply_text(txt, parse_mode=ParseMode.HTML)

async def resetwarns(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, ctx):
        return await update.message.reply_text("🚫 Only admins can reset warnings!")
    user, _ = await resolve(update, ctx)
    if not user:
        return
    data = load()
    data = ensure_keys(data)
    cid, uid = str(update.effective_chat.id), str(user.id)
    data["warns"].setdefault(cid, {})[uid] = []
    save(data)
    await update.message.reply_text(
        f"✅ All warnings cleared for {mention(user)}!",
        parse_mode=ParseMode.HTML
    )

# ─── REPORT ────────────────────────────────────────────────────────────────────
async def report(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Any user can report — goes to all admins in DM"""
    if update.effective_chat.type == "private":
        return await update.message.reply_text("❌ Use this command inside a group!")

    reporter = update.effective_user
    chat = update.effective_chat
    reason = " ".join(ctx.args) if ctx.args else ""

    if update.message.reply_to_message:
        reported_user = update.message.reply_to_message.from_user
        reported_msg = update.message.reply_to_message.text or "[media/file]"
    else:
        await update.message.reply_text("❌ Reply to a message first, then use /report!")
        return

    if reported_user.id == reporter.id:
        return await update.message.reply_text("😅 You can't report yourself!")

    if reported_user.is_bot:
        return await update.message.reply_text("🤖 You can't report a bot!")

    report_text = (
        f"🚨 <b>NEW REPORT!</b>\n\n"
        f"📢 <b>Group:</b> {chat.title} (<code>{chat.id}</code>)\n"
        f"👤 <b>Reported User:</b> {mention(reported_user)} (<code>{reported_user.id}</code>)\n"
        f"📝 <b>Message:</b> {reported_msg[:200]}\n"
        f"👮 <b>Reporter:</b> {mention(reporter)}\n"
        f"📋 <b>Reason:</b> {reason or 'Not specified'}\n\n"
        f"⚡ <b>Quick Actions:</b>"
    )

    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔨 Ban", callback_data=f"rpt_ban_{chat.id}_{reported_user.id}"),
            InlineKeyboardButton("👢 Kick", callback_data=f"rpt_kick_{chat.id}_{reported_user.id}"),
        ],
        [
            InlineKeyboardButton("🔇 Mute 1hr", callback_data=f"rpt_mute_{chat.id}_{reported_user.id}"),
            InlineKeyboardButton("⚠️ Warn", callback_data=f"rpt_warn_{chat.id}_{reported_user.id}"),
        ],
        [InlineKeyboardButton("✅ Dismiss", callback_data="rpt_dismiss")]
    ])

    # Send to all admins in DM
    try:
        admins = await chat.get_administrators()
        sent_to = 0
        for admin in admins:
            if admin.user.is_bot:
                continue
            try:
                await ctx.bot.send_message(admin.user.id, report_text, parse_mode=ParseMode.HTML, reply_markup=kb)
                sent_to += 1
            except:
                pass  # User hasn't started bot

        await update.message.reply_text(
            f"✅ <b>Report submitted!</b> {sent_to} admin(s) have been notified.\n"
            f"<i>Admins will take action shortly.</i>",
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        await update.message.reply_text("❌ Failed to send report. Please tag an admin manually.")

async def report_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Handle report action buttons"""
    q = update.callback_query
    await q.answer()

    if q.data == "rpt_dismiss":
        await q.message.edit_text("✅ Report dismissed.", reply_markup=None)
        return

    parts = q.data.split("_")
    action = parts[1]
    chat_id = int(parts[2])
    user_id = int(parts[3])

    try:
        if action == "ban":
            await ctx.bot.ban_chat_member(chat_id, user_id)
            await q.message.edit_text(f"🔨 User ({user_id}) has been banned!", reply_markup=None)
        elif action == "kick":
            await ctx.bot.ban_chat_member(chat_id, user_id)
            await ctx.bot.unban_chat_member(chat_id, user_id)
            await q.message.edit_text(f"👢 User ({user_id}) has been kicked!", reply_markup=None)
        elif action == "mute":
            until = datetime.now() + timedelta(hours=1)
            await ctx.bot.restrict_chat_member(chat_id, user_id, ChatPermissions(can_send_messages=False), until_date=until)
            await q.message.edit_text(f"🔇 User ({user_id}) muted for 1 hour!", reply_markup=None)
        elif action == "warn":
            data = load()
            data = ensure_keys(data)
            cid_str = str(chat_id)
            uid_str = str(user_id)
            data["warns"].setdefault(cid_str, {}).setdefault(uid_str, [])
            data["warns"][cid_str][uid_str].append({"reason": "Report action", "time": datetime.now().isoformat()})
            count = len(data["warns"][cid_str][uid_str])
            if count >= MAX_WARNS:
                await ctx.bot.ban_chat_member(chat_id, user_id)
                data["warns"][cid_str][uid_str] = []
            save(data)
            await q.message.edit_text(f"⚠️ User ({user_id}) warned! ({count}/{MAX_WARNS})", reply_markup=None)
    except Exception as e:
        await q.message.reply_text(f"❌ Action failed: {e}")

# ─── LOCK / UNLOCK ─────────────────────────────────────────────────────────────
LOCK_TYPES = {
    "msg": "can_send_messages",
    "media": "can_send_media_messages",
    "polls": "can_send_polls",
    "links": "can_add_web_page_previews",
    "stickers": "can_send_other_messages",
    "all": None
}

async def lock(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, ctx):
        return await update.message.reply_text("🚫 Only admins can lock the group!")
    if not await bot_ok(update, ctx):
        return await update.message.reply_text("❌ Make me an admin first!")
    if not ctx.args:
        return await update.message.reply_text(
            "🔒 <b>Usage:</b> /lock &lt;type&gt;\n"
            "📋 <b>Types:</b> msg, media, polls, links, stickers, all",
            parse_mode=ParseMode.HTML
        )
    lock_type = ctx.args[0].lower()
    if lock_type not in LOCK_TYPES:
        return await update.message.reply_text("❌ Valid types: msg, media, polls, links, stickers, all")

    if lock_type == "all":
        perms = ChatPermissions(
            can_send_messages=False, can_send_media_messages=False,
            can_send_polls=False, can_send_other_messages=False,
            can_add_web_page_previews=False
        )
    else:
        current_perms = update.effective_chat.permissions
        kwargs = {
            "can_send_messages": current_perms.can_send_messages if current_perms else True,
            "can_send_media_messages": current_perms.can_send_media_messages if current_perms else True,
            "can_send_polls": current_perms.can_send_polls if current_perms else True,
            "can_send_other_messages": current_perms.can_send_other_messages if current_perms else True,
            "can_add_web_page_previews": current_perms.can_add_web_page_previews if current_perms else True,
        }
        kwargs[LOCK_TYPES[lock_type]] = False
        perms = ChatPermissions(**kwargs)

    await update.effective_chat.set_permissions(perms)
    await update.message.reply_text(
        f"🔒 <b>{lock_type.upper()}</b> is now locked!\n"
        f"Only admins can use this feature.",
        parse_mode=ParseMode.HTML
    )

async def unlock(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, ctx):
        return await update.message.reply_text("🚫 Only admins can unlock the group!")
    if not await bot_ok(update, ctx):
        return await update.message.reply_text("❌ Make me an admin first!")
    if not ctx.args:
        return await update.message.reply_text(
            "🔓 <b>Usage:</b> /unlock &lt;type&gt;\n"
            "📋 <b>Types:</b> msg, media, polls, links, stickers, all",
            parse_mode=ParseMode.HTML
        )
    lock_type = ctx.args[0].lower()
    if lock_type not in LOCK_TYPES:
        return await update.message.reply_text("❌ Valid types: msg, media, polls, links, stickers, all")

    if lock_type == "all":
        perms = ChatPermissions(
            can_send_messages=True, can_send_media_messages=True,
            can_send_polls=True, can_send_other_messages=True,
            can_add_web_page_previews=True
        )
    else:
        current_perms = update.effective_chat.permissions
        kwargs = {
            "can_send_messages": current_perms.can_send_messages if current_perms else True,
            "can_send_media_messages": current_perms.can_send_media_messages if current_perms else True,
            "can_send_polls": current_perms.can_send_polls if current_perms else True,
            "can_send_other_messages": current_perms.can_send_other_messages if current_perms else True,
            "can_add_web_page_previews": current_perms.can_add_web_page_previews if current_perms else True,
        }
        kwargs[LOCK_TYPES[lock_type]] = True
        perms = ChatPermissions(**kwargs)

    await update.effective_chat.set_permissions(perms)
    await update.message.reply_text(
        f"🔓 <b>{lock_type.upper()}</b> is now unlocked!\n"
        f"All members can use this feature again.",
        parse_mode=ParseMode.HTML
    )

# ─── GROUP OWNER PRIVATE PANEL ─────────────────────────────────────────────────
async def panel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Group owner panel — works in both group and private chat"""
    uid = update.effective_user.id
    chat = update.effective_chat

    if chat.type == "private":
        # Show list of groups where user is owner
        data = load()
        data = ensure_keys(data)
        owner_groups = [
            (cid, info) for cid, info in data["groups"].items()
            if info.get("owner_id") == uid or uid == OWNER_ID
        ]
        if not owner_groups:
            return await update.message.reply_text(
                "❌ <b>No groups found where you are the owner.</b>\n\n"
                "💡 Go to your group and type /panel there first so the bot registers you as owner!",
                parse_mode=ParseMode.HTML
            )
        buttons = []
        for cid, info in owner_groups:
            buttons.append([InlineKeyboardButton(
                f"📂 {info.get('title', 'Unknown Group')}",
                callback_data=f"grppanel_{cid}"
            )])
        await update.message.reply_text(
            "👑 <b>Your Groups</b>\n\nSelect a group to manage:",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(buttons)
        )
        return

    # In group — check if owner or bot owner
    if not await is_group_owner(update) and uid != OWNER_ID:
        return await update.message.reply_text(
            "🚫 Only the group owner can use /panel!\n"
            "💡 Admins can use /stats instead."
        )

    # FIX: Send panel link to DM for easier access
    bot_username = ctx.bot.username
    deep_link = f"https://t.me/{bot_username}?start=panel_{chat.id}"
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("👑 Open Panel in DM", url=deep_link)
    ]])
    await update.message.reply_text(
        f"👑 <b>Owner Panel Ready!</b>\n\n"
        f"Click the button below to open your full control panel in private chat 👇",
        parse_mode=ParseMode.HTML,
        reply_markup=kb
    )

async def show_group_panel_inline(update, ctx, cid):
    """Show full panel for a group"""
    data = load()
    data = ensure_keys(data)
    s = data["stats"].get(cid, {})
    msg_count = sum(data["msg_count"].get(cid, {}).values())

    try:
        chat_obj = await ctx.bot.get_chat(int(cid))
        member_count = await ctx.bot.get_chat_member_count(int(cid))
        group_title = chat_obj.title or "Unknown"
    except:
        member_count = 0
        group_title = data["groups"].get(cid, {}).get("title", "Unknown")

    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📊 Full Stats", callback_data=f"gp_stats_{cid}"),
            InlineKeyboardButton("👥 Members", callback_data=f"gp_members_{cid}"),
        ],
        [
            InlineKeyboardButton("👮 Admins", callback_data=f"gp_admins_{cid}"),
            InlineKeyboardButton("🚫 Blacklist", callback_data=f"gp_bl_{cid}"),
        ],
        [
            InlineKeyboardButton("🔒 Lock Group", callback_data=f"gp_lock_{cid}"),
            InlineKeyboardButton("🔓 Unlock Group", callback_data=f"gp_unlock_{cid}"),
        ],
        [InlineKeyboardButton("🔄 Refresh", callback_data=f"grppanel_{cid}")]
    ])

    txt = (
        f"👑 <b>OWNER CONTROL PANEL</b>\n"
        f"📂 <b>{group_title}</b>\n\n"
        f"👥 Members: <b>{member_count}</b>\n"
        f"💬 Total Messages: <b>{msg_count}</b>\n\n"
        f"📊 <b>Moderation Stats:</b>\n"
        f"🔨 Bans: <b>{s.get('bans', 0)}</b>\n"
        f"👢 Kicks: <b>{s.get('kicks', 0)}</b>\n"
        f"🔇 Mutes: <b>{s.get('mutes', 0)}</b>\n"
        f"⚠️ Warns: <b>{s.get('warns', 0)}</b>\n\n"
        f"<i>🕐 Last updated: {datetime.now().strftime('%d %b %Y %H:%M')}</i>"
    )
    await update.effective_message.reply_text(txt, parse_mode=ParseMode.HTML, reply_markup=kb)

async def show_group_panel(update, ctx, cid):
    """Called from deep link in DM"""
    uid = update.effective_user.id
    data = load()
    data = ensure_keys(data)
    group_info = data["groups"].get(cid, {})
    if group_info.get("owner_id") != uid and uid != OWNER_ID:
        return await update.message.reply_text("🚫 This panel is only for the group owner!")
    await show_group_panel_inline(update, ctx, cid)

async def group_panel_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Handle group panel callback buttons"""
    q = update.callback_query
    await q.answer()
    uid = update.effective_user.id
    parts = q.data.split("_")

    # Handle grppanel_ prefix (2 parts: grppanel + cid)
    if q.data.startswith("grppanel_"):
        cid = q.data.replace("grppanel_", "")
        data = load()
        data = ensure_keys(data)
        group_info = data["groups"].get(cid, {})
        if group_info.get("owner_id") != uid and uid != OWNER_ID:
            return await q.message.reply_text("🚫 Only the group owner can use this!")
        await show_group_panel_inline(update, ctx, cid)
        return

    # Handle gp_ prefix (3 parts: gp + action + cid)
    if q.data.startswith("gp_"):
        # Split carefully — cid can be negative (supergroups)
        remainder = q.data[3:]  # Remove "gp_"
        underscore_idx = remainder.index("_")
        action = remainder[:underscore_idx]
        cid = remainder[underscore_idx + 1:]

        data = load()
        data = ensure_keys(data)
        group_info = data["groups"].get(cid, {})

        if group_info.get("owner_id") != uid and uid != OWNER_ID:
            return await q.message.reply_text("🚫 Only the group owner can use this!")

        if action == "stats":
            s = data["stats"].get(cid, {})
            msg_count = sum(data["msg_count"].get(cid, {}).values())
            warns_data = data["warns"].get(cid, {})
            warned_users = sum(1 for w in warns_data.values() if w)
            await q.message.reply_text(
                f"📊 <b>Detailed Stats</b>\n\n"
                f"💬 Total Messages: <b>{msg_count}</b>\n"
                f"🔨 Bans: <b>{s.get('bans', 0)}</b>\n"
                f"👢 Kicks: <b>{s.get('kicks', 0)}</b>\n"
                f"🔇 Mutes: <b>{s.get('mutes', 0)}</b>\n"
                f"⚠️ Total Warns: <b>{s.get('warns', 0)}</b>\n"
                f"👤 Users currently warned: <b>{warned_users}</b>",
                parse_mode=ParseMode.HTML
            )
        elif action == "members":
            try:
                count = await ctx.bot.get_chat_member_count(int(cid))
                await q.message.reply_text(f"👥 <b>Total Members:</b> {count}", parse_mode=ParseMode.HTML)
            except:
                await q.message.reply_text("❌ Could not fetch member count.")

        elif action == "admins":
            try:
                admins = await ctx.bot.get_chat_administrators(int(cid))
                txt = "👮 <b>Group Admins:</b>\n\n"
                for a in admins:
                    if not a.user.is_bot:
                        role = "👑 Owner" if a.status == "creator" else "⭐ Admin"
                        txt += f"• {mention(a.user)} — {role}\n"
                await q.message.reply_text(txt, parse_mode=ParseMode.HTML)
            except:
                await q.message.reply_text("❌ Could not fetch admin list.")

        elif action == "bl":
            bl = data["blacklist"].get(cid, [])
            if not bl:
                await q.message.reply_text("🚫 The blacklist is empty.")
            else:
                await q.message.reply_text(
                    "🚫 <b>Blacklisted Words:</b>\n\n" + "\n".join(f"• <code>{w}</code>" for w in bl),
                    parse_mode=ParseMode.HTML
                )

        elif action == "lock":
            try:
                await ctx.bot.set_chat_permissions(int(cid), ChatPermissions(can_send_messages=False))
                await q.message.reply_text("🔒 Group locked! Only admins can send messages now.")
            except:
                await q.message.reply_text("❌ Failed to lock. Make sure I'm an admin in that group.")

        elif action == "unlock":
            try:
                await ctx.bot.set_chat_permissions(int(cid), ChatPermissions(
                    can_send_messages=True, can_send_media_messages=True,
                    can_send_polls=True, can_send_other_messages=True,
                    can_add_web_page_previews=True
                ))
                await q.message.reply_text("🔓 Group unlocked! Everyone can send messages again.")
            except:
                await q.message.reply_text("❌ Failed to unlock.")

# ─── RULES ─────────────────────────────────────────────────────────────────────
async def setrules(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, ctx):
        return await update.message.reply_text("🚫 Only admins can set rules!")
    if not ctx.args:
        return await update.message.reply_text(
            "📋 <b>Usage:</b> /setrules &lt;rules text&gt;",
            parse_mode=ParseMode.HTML
        )
    data = load()
    data = ensure_keys(data)
    data["rules"][str(update.effective_chat.id)] = " ".join(ctx.args)
    save(data)
    await update.message.reply_text("✅ Group rules have been updated!")

async def rules(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    data = load()
    data = ensure_keys(data)
    r = data["rules"].get(str(update.effective_chat.id))
    if not r:
        return await update.message.reply_text(
            "📋 No rules have been set yet.\n👮 Admin: use /setrules &lt;text&gt; to set them.",
            parse_mode=ParseMode.HTML
        )
    await update.message.reply_text(
        f"📋 <b>{update.effective_chat.title} — Rules:</b>\n\n{r}",
        parse_mode=ParseMode.HTML
    )

# ─── BLACKLIST ─────────────────────────────────────────────────────────────────
async def addbl(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, ctx):
        return await update.message.reply_text("🚫 Only admins can modify the blacklist!")
    if not ctx.args:
        return await update.message.reply_text("📝 Usage: /addbl &lt;word&gt;", parse_mode=ParseMode.HTML)
    word = ctx.args[0].lower()
    data = load()
    data = ensure_keys(data)
    cid = str(update.effective_chat.id)
    data["blacklist"].setdefault(cid, [])
    if word not in data["blacklist"][cid]:
        data["blacklist"][cid].append(word)
        save(data)
        await update.message.reply_text(f"✅ Word <code>{word}</code> added to blacklist!", parse_mode=ParseMode.HTML)
    else:
        await update.message.reply_text(f"ℹ️ Word <code>{word}</code> is already blacklisted!", parse_mode=ParseMode.HTML)

async def delbl(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, ctx):
        return await update.message.reply_text("🚫 Only admins can modify the blacklist!")
    if not ctx.args:
        return await update.message.reply_text("📝 Usage: /delbl &lt;word&gt;", parse_mode=ParseMode.HTML)
    word = ctx.args[0].lower()
    data = load()
    data = ensure_keys(data)
    cid = str(update.effective_chat.id)
    if word in data["blacklist"].get(cid, []):
        data["blacklist"][cid].remove(word)
        save(data)
        await update.message.reply_text(f"🗑️ Word <code>{word}</code> removed from blacklist!", parse_mode=ParseMode.HTML)
    else:
        await update.message.reply_text(f"❌ Word <code>{word}</code> is not in the blacklist.", parse_mode=ParseMode.HTML)

async def blacklist_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    data = load()
    data = ensure_keys(data)
    bl = data["blacklist"].get(str(update.effective_chat.id), [])
    if not bl:
        return await update.message.reply_text("🚫 The blacklist is empty.")
    await update.message.reply_text(
        "🚫 <b>Blacklisted Words:</b>\n\n" + "\n".join(f"• <code>{w}</code>" for w in bl),
        parse_mode=ParseMode.HTML
    )

async def check_blacklist(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return
    if await is_admin(update, ctx):
        return
    data = load()
    data = ensure_keys(data)
    bl = data["blacklist"].get(str(update.effective_chat.id), [])
    text = update.message.text.lower()
    for word in bl:
        if word in text:
            try:
                await update.message.delete()
            except:
                pass
            await update.effective_chat.send_message(
                f"⚠️ {mention(update.effective_user)}, that word is not allowed here!",
                parse_mode=ParseMode.HTML
            )
            return

# ─── ANTI-FLOOD ────────────────────────────────────────────────────────────────
async def setflood(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, ctx):
        return await update.message.reply_text("🚫 Only admins can configure anti-flood!")
    if not ctx.args or not ctx.args[0].isdigit():
        return await update.message.reply_text(
            "📝 Usage: /setflood &lt;number&gt;\n"
            "💡 Set to 0 to disable. Example: /setflood 5",
            parse_mode=ParseMode.HTML
        )
    limit = int(ctx.args[0])
    data = load()
    data = ensure_keys(data)
    data["antiflood"][str(update.effective_chat.id)] = limit
    save(data)
    if limit == 0:
        await update.message.reply_text("✅ Anti-flood has been <b>disabled</b>.", parse_mode=ParseMode.HTML)
    else:
        await update.message.reply_text(
            f"✅ Anti-flood set to <b>{limit} messages per 10 seconds</b>.\n"
            f"⚠️ Flooders will be muted for 5 minutes.",
            parse_mode=ParseMode.HTML
        )

async def check_flood(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not update.message or await is_admin(update, ctx):
        return
    data = load()
    data = ensure_keys(data)
    cid = str(update.effective_chat.id)
    limit = data["antiflood"].get(cid, 0)
    if limit == 0:
        return
    uid = str(update.effective_user.id)
    now = datetime.now().timestamp()
    t = data["flood_tracker"].setdefault(cid, {}).setdefault(uid, {"count": 0, "reset": now + 10})
    if now > t["reset"]:
        t["count"] = 1
        t["reset"] = now + 10
    else:
        t["count"] += 1
    if t["count"] >= limit:
        t["count"] = 0
        try:
            await update.effective_chat.restrict_member(
                update.effective_user.id,
                ChatPermissions(can_send_messages=False),
                until_date=datetime.now() + timedelta(minutes=5)
            )
            await update.effective_chat.send_message(
                f"🌊 {mention(update.effective_user)} was muted for 5 minutes due to flooding!",
                parse_mode=ParseMode.HTML
            )
        except:
            pass
    save(data)

# ─── NOTES ─────────────────────────────────────────────────────────────────────
async def save_note(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, ctx):
        return await update.message.reply_text("🚫 Only admins can save notes!")
    if len(ctx.args) < 2:
        return await update.message.reply_text(
            "📝 Usage: /save &lt;name&gt; &lt;content&gt;",
            parse_mode=ParseMode.HTML
        )
    name, content = ctx.args[0].lower(), " ".join(ctx.args[1:])
    data = load()
    data = ensure_keys(data)
    data["notes"].setdefault(str(update.effective_chat.id), {})[name] = content
    save(data)
    await update.message.reply_text(
        f"📝 Note <code>{name}</code> saved successfully!",
        parse_mode=ParseMode.HTML
    )

async def get_note(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        return await update.message.reply_text("📝 Usage: /get &lt;name&gt;", parse_mode=ParseMode.HTML)
    name = ctx.args[0].lower()
    data = load()
    data = ensure_keys(data)
    note = data["notes"].get(str(update.effective_chat.id), {}).get(name)
    if not note:
        return await update.message.reply_text(
            f"❌ Note <code>{name}</code> not found.",
            parse_mode=ParseMode.HTML
        )
    await update.message.reply_text(f"📝 <b>{name}:</b>\n\n{note}", parse_mode=ParseMode.HTML)

async def notes_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    data = load()
    data = ensure_keys(data)
    notes = data["notes"].get(str(update.effective_chat.id), {})
    if not notes:
        return await update.message.reply_text("📝 No saved notes in this group.")
    await update.message.reply_text(
        "📝 <b>Saved Notes:</b>\n\n" + "\n".join(f"• <code>{n}</code>" for n in notes) +
        "\n\n💡 Use /get &lt;name&gt; to retrieve a note.",
        parse_mode=ParseMode.HTML
    )

async def delnote(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, ctx):
        return await update.message.reply_text("🚫 Only admins can delete notes!")
    if not ctx.args:
        return await update.message.reply_text("📝 Usage: /delnote &lt;name&gt;", parse_mode=ParseMode.HTML)
    name = ctx.args[0].lower()
    data = load()
    data = ensure_keys(data)
    cid = str(update.effective_chat.id)
    if name in data["notes"].get(cid, {}):
        del data["notes"][cid][name]
        save(data)
        await update.message.reply_text(
            f"🗑️ Note <code>{name}</code> has been deleted!",
            parse_mode=ParseMode.HTML
        )
    else:
        await update.message.reply_text(f"❌ Note <code>{name}</code> not found.", parse_mode=ParseMode.HTML)

# ─── FILTERS ───────────────────────────────────────────────────────────────────
async def add_filter(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, ctx):
        return await update.message.reply_text("🚫 Only admins can add filters!")
    if len(ctx.args) < 2:
        return await update.message.reply_text(
            "📝 Usage: /filter &lt;keyword&gt; &lt;reply text&gt;",
            parse_mode=ParseMode.HTML
        )
    kw, reply = ctx.args[0].lower(), " ".join(ctx.args[1:])
    data = load()
    data = ensure_keys(data)
    data["filters"].setdefault(str(update.effective_chat.id), {})[kw] = reply
    save(data)
    await update.message.reply_text(
        f"✅ Filter <code>{kw}</code> added successfully!",
        parse_mode=ParseMode.HTML
    )

async def del_filter(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, ctx):
        return await update.message.reply_text("🚫 Only admins can delete filters!")
    if not ctx.args:
        return await update.message.reply_text("📝 Usage: /delfilter &lt;keyword&gt;", parse_mode=ParseMode.HTML)
    kw = ctx.args[0].lower()
    data = load()
    data = ensure_keys(data)
    cid = str(update.effective_chat.id)
    if kw in data["filters"].get(cid, {}):
        del data["filters"][cid][kw]
        save(data)
        await update.message.reply_text(
            f"🗑️ Filter <code>{kw}</code> removed!",
            parse_mode=ParseMode.HTML
        )
    else:
        await update.message.reply_text(f"❌ Filter <code>{kw}</code> not found.", parse_mode=ParseMode.HTML)

async def filters_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    data = load()
    data = ensure_keys(data)
    f = data["filters"].get(str(update.effective_chat.id), {})
    if not f:
        return await update.message.reply_text("🔍 No active filters in this group.")
    await update.message.reply_text(
        "🔍 <b>Active Filters:</b>\n\n" + "\n".join(f"• <code>{k}</code>" for k in f),
        parse_mode=ParseMode.HTML
    )

async def check_filters(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return
    data = load()
    data = ensure_keys(data)
    f = data["filters"].get(str(update.effective_chat.id), {})
    text = update.message.text.lower()
    for kw, reply in f.items():
        if kw in text:
            await update.message.reply_text(reply)
            break

# ─── PROMOTE / DEMOTE ──────────────────────────────────────────────────────────
async def promote(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, ctx):
        return await update.message.reply_text("🚫 Only admins can promote members!")
    if not await bot_ok(update, ctx):
        return await update.message.reply_text("❌ Make me an admin first!")
    user, _ = await resolve(update, ctx)
    if not user:
        return
    if user.id == ctx.bot.id:
        return await update.message.reply_text("😅 I can't promote myself!")
    try:
        await update.effective_chat.promote_member(
            user.id,
            can_change_info=True, can_delete_messages=True,
            can_invite_users=True, can_restrict_members=True,
            can_pin_messages=True, can_manage_chat=True
        )
        await update.message.reply_text(
            f"⭐ {mention(user)} has been promoted to Admin!\n"
            f"👮 <b>By:</b> {mention(update.effective_user)}",
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        await update.message.reply_text(f"❌ Failed to promote: {e}")

async def demote(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, ctx):
        return await update.message.reply_text("🚫 Only admins can demote members!")
    if not await bot_ok(update, ctx):
        return await update.message.reply_text("❌ Make me an admin first!")
    user, _ = await resolve(update, ctx)
    if not user:
        return
    try:
        await update.effective_chat.promote_member(
            user.id,
            can_change_info=False, can_delete_messages=False,
            can_invite_users=False, can_restrict_members=False,
            can_pin_messages=False
        )
        await update.message.reply_text(
            f"⬇️ {mention(user)} has been demoted from Admin.\n"
            f"👮 <b>By:</b> {mention(update.effective_user)}",
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        await update.message.reply_text(f"❌ Failed to demote: {e}")

# ─── PIN / UNPIN / DEL / PURGE ─────────────────────────────────────────────────
async def pin(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, ctx):
        return await update.message.reply_text("🚫 Only admins can pin messages!")
    if not update.message.reply_to_message:
        return await update.message.reply_text("📌 Reply to a message to pin it.")
    notify = "--notify" in (ctx.args or [])
    try:
        await update.message.reply_to_message.pin(disable_notification=not notify)
        await update.message.reply_text("📌 Message pinned successfully!")
    except Exception as e:
        await update.message.reply_text(f"❌ Failed to pin: {e}")

async def unpin(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, ctx):
        return await update.message.reply_text("🚫 Only admins can unpin messages!")
    try:
        await update.effective_chat.unpin_message()
        await update.message.reply_text("📌 Latest pinned message has been unpinned!")
    except Exception as e:
        await update.message.reply_text(f"❌ Failed to unpin: {e}")

async def delete_msg(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, ctx):
        return await update.message.reply_text("🚫 Only admins can delete messages!")
    if not update.message.reply_to_message:
        return await update.message.reply_text("🗑️ Reply to a message to delete it.")
    try:
        await update.message.reply_to_message.delete()
        await update.message.delete()
    except:
        await update.message.reply_text("❌ Failed to delete message.")

async def purge(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Delete messages from replied message to latest"""
    if not await is_admin(update, ctx):
        return await update.message.reply_text("🚫 Only admins can purge messages!")
    if not update.message.reply_to_message:
        return await update.message.reply_text("📌 Reply to the message you want to start purging from.")
    if not await bot_ok(update, ctx):
        return await update.message.reply_text("❌ Make me an admin first!")

    start_id = update.message.reply_to_message.message_id
    end_id = update.message.message_id
    count = 0
    for mid in range(start_id, end_id + 1):
        try:
            await ctx.bot.delete_message(update.effective_chat.id, mid)
            count += 1
        except:
            pass

    notif = await update.effective_chat.send_message(f"🗑️ Purged {count} messages successfully!")
    await asyncio.sleep(3)
    try:
        await notif.delete()
    except:
        pass

# ─── INFO / STATS ──────────────────────────────────────────────────────────────
async def adminlist(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        admins = await update.effective_chat.get_administrators()
        txt = f"👑 <b>{update.effective_chat.title} — Admin List:</b>\n\n"
        for a in admins:
            if a.user.is_bot:
                continue
            title = a.custom_title or ("👑 Owner" if a.status == "creator" else "⭐ Admin")
            txt += f"• {mention(a.user)} — <i>{title}</i>\n"
        await update.message.reply_text(txt, parse_mode=ParseMode.HTML)
    except:
        await update.message.reply_text("❌ Could not fetch admin list.")

async def chatinfo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    try:
        count = await chat.get_member_count()
    except:
        count = "N/A"
    data = load()
    data = ensure_keys(data)
    cid = str(chat.id)
    msg_count = sum(data["msg_count"].get(cid, {}).values())
    await update.message.reply_text(
        f"ℹ️ <b>Group Information</b>\n\n"
        f"📛 <b>Name:</b> {chat.title}\n"
        f"🆔 <b>ID:</b> <code>{chat.id}</code>\n"
        f"👥 <b>Members:</b> {count}\n"
        f"💬 <b>Total Messages:</b> {msg_count}\n"
        f"🔗 <b>Username:</b> @{chat.username or 'Private group'}",
        parse_mode=ParseMode.HTML
    )

async def userinfo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.message.reply_to_message:
        user = update.message.reply_to_message.from_user
    else:
        user = update.effective_user
    try:
        member = await update.effective_chat.get_member(user.id)
        role = {"creator": "👑 Owner", "administrator": "⭐ Admin", "member": "👤 Member"}.get(member.status, "👤 Member")
    except:
        role = "Unknown"
    data = load()
    data = ensure_keys(data)
    cid = str(update.effective_chat.id)
    warns = len(data["warns"].get(cid, {}).get(str(user.id), []))
    await update.message.reply_text(
        f"👤 <b>User Information</b>\n\n"
        f"📛 <b>Name:</b> {user.full_name}\n"
        f"🆔 <b>ID:</b> <code>{user.id}</code>\n"
        f"🔖 <b>Username:</b> @{user.username or 'No username'}\n"
        f"🤖 <b>Bot:</b> {'Yes' if user.is_bot else 'No'}\n"
        f"📌 <b>Role:</b> {role}\n"
        f"⚠️ <b>Warnings:</b> {warns}/{MAX_WARNS}",
        parse_mode=ParseMode.HTML
    )

async def stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, ctx):
        return await update.message.reply_text("🚫 Only admins can view stats!")
    data = load()
    data = ensure_keys(data)
    cid = str(update.effective_chat.id)
    s = data["stats"].get(cid, {})
    msg_count = sum(data["msg_count"].get(cid, {}).values())
    try:
        member_count = await update.effective_chat.get_member_count()
    except:
        member_count = "N/A"
    await update.message.reply_text(
        f"📊 <b>Group Stats — {update.effective_chat.title}</b>\n\n"
        f"👥 <b>Members:</b> {member_count}\n"
        f"💬 <b>Total Messages:</b> {msg_count}\n\n"
        f"📋 <b>Moderation Actions:</b>\n"
        f"🔨 Bans: <b>{s.get('bans', 0)}</b>\n"
        f"👢 Kicks: <b>{s.get('kicks', 0)}</b>\n"
        f"🔇 Mutes: <b>{s.get('mutes', 0)}</b>\n"
        f"⚠️ Warns: <b>{s.get('warns', 0)}</b>",
        parse_mode=ParseMode.HTML
    )

async def get_id(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.message.reply_to_message:
        u = update.message.reply_to_message.from_user
        await update.message.reply_text(
            f"👤 {mention(u)}\n🆔 <b>User ID:</b> <code>{u.id}</code>",
            parse_mode=ParseMode.HTML
        )
    else:
        await update.message.reply_text(
            f"🆔 <b>Your ID:</b> <code>{update.effective_user.id}</code>\n"
            f"💬 <b>Chat ID:</b> <code>{update.effective_chat.id}</code>",
            parse_mode=ParseMode.HTML
        )

# ─── TOP USERS ─────────────────────────────────────────────────────────────────
async def topusers(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Show top 10 most active users"""
    if not await is_admin(update, ctx):
        return await update.message.reply_text("🚫 Only admins can view this!")
    data = load()
    data = ensure_keys(data)
    cid = str(update.effective_chat.id)
    user_msgs = data["msg_count"].get(cid, {})
    if not user_msgs:
        return await update.message.reply_text("💬 No message data yet.")
    sorted_users = sorted(user_msgs.items(), key=lambda x: x[1], reverse=True)[:10]
    txt = "🏆 <b>Top Active Members:</b>\n\n"
    medals = ["🥇", "🥈", "🥉"]
    for i, (uid, count) in enumerate(sorted_users):
        medal = medals[i] if i < 3 else f"{i+1}."
        try:
            member = await update.effective_chat.get_member(int(uid))
            name = member.user.full_name[:20]
        except:
            name = f"User {uid}"
        txt += f"{medal} {name} — <b>{count}</b> messages\n"
    await update.message.reply_text(txt, parse_mode=ParseMode.HTML)

# ─── BROADCAST ─────────────────────────────────────────────────────────────────
async def broadcast(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return await update.message.reply_text("🚫 Only the bot owner can broadcast!")
    if not ctx.args:
        return await update.message.reply_text("📢 Usage: /broadcast &lt;message&gt;", parse_mode=ParseMode.HTML)
    msg = " ".join(ctx.args)
    data = load()
    data = ensure_keys(data)
    sent, failed = 0, 0
    for cid in data["groups"]:
        try:
            await ctx.bot.send_message(
                int(cid),
                f"📢 <b>Announcement:</b>\n\n{msg}",
                parse_mode=ParseMode.HTML
            )
            sent += 1
        except:
            failed += 1
    await update.message.reply_text(
        f"✅ <b>Broadcast Complete!</b>\n"
        f"📤 Sent: <b>{sent}</b>\n"
        f"❌ Failed: <b>{failed}</b>",
        parse_mode=ParseMode.HTML
    )

async def mygroups(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Bot owner: see all groups"""
    if update.effective_user.id != OWNER_ID:
        return await update.message.reply_text("🚫 Only the bot owner can use this!")
    data = load()
    data = ensure_keys(data)
    if not data["groups"]:
        return await update.message.reply_text("❌ No groups found.")
    txt = f"📂 <b>All Groups ({len(data['groups'])}):</b>\n\n"
    for cid, info in data["groups"].items():
        txt += f"• <b>{info.get('title', 'Unknown')}</b> — <code>{cid}</code>\n"
    await update.message.reply_text(txt, parse_mode=ParseMode.HTML)

# ─── MESSAGE COUNTER ───────────────────────────────────────────────────────────
async def count_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Count messages per user per group"""
    if not update.message or not update.effective_user:
        return
    if update.effective_chat.type not in ("group", "supergroup"):
        return
    data = load()
    data = ensure_keys(data)
    cid = str(update.effective_chat.id)
    uid = str(update.effective_user.id)
    data["msg_count"].setdefault(cid, {})
    data["msg_count"][cid][uid] = data["msg_count"][cid].get(uid, 0) + 1
    save(data)

# ─── CALLBACK HANDLER ──────────────────────────────────────────────────────────
async def panel_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    if q.data == "show_help":
        await q.message.reply_text(HELP, parse_mode=ParseMode.HTML)
        return

    if q.data == "show_about":
        await q.message.reply_text(
            "🤖 <b>Group Manager Bot</b>\n\n"
            "A powerful Telegram group management bot.\n\n"
            "✅ Ban / Kick / Mute / Warn members\n"
            "✅ Anti-flood & blacklist protection\n"
            "✅ Report system for users\n"
            "✅ Owner control panel (in DM)\n"
            "✅ Notes & keyword filters\n"
            "✅ Message activity tracking\n"
            "✅ Group lock / unlock\n\n"
            "💪 Built with ❤️",
            parse_mode=ParseMode.HTML
        )
        return

    if q.data == "show_my_panel":
        uid = update.effective_user.id
        data = load()
        data = ensure_keys(data)
        owner_groups = [
            (cid, info) for cid, info in data["groups"].items()
            if info.get("owner_id") == uid or uid == OWNER_ID
        ]
        if not owner_groups:
            await q.message.reply_text(
                "❌ <b>No groups found where you are owner.</b>\n\n"
                "💡 Add the bot to your group, make it admin, then type /panel in the group first!",
                parse_mode=ParseMode.HTML
            )
            return
        buttons = [[InlineKeyboardButton(
            f"📂 {info.get('title', 'Unknown')}",
            callback_data=f"grppanel_{cid}"
        )] for cid, info in owner_groups]
        await q.message.reply_text(
            "👑 <b>Your Groups</b>\n\nSelect a group to manage:",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(buttons)
        )
        return

    if q.data.startswith("show_rules_"):
        cid = q.data.replace("show_rules_", "")
        data = load()
        data = ensure_keys(data)
        r = data["rules"].get(cid, "No rules have been set for this group yet.")
        await q.message.reply_text(f"📋 <b>Group Rules:</b>\n\n{r}", parse_mode=ParseMode.HTML)
        return

    if q.data.startswith("show_admins_"):
        try:
            admins = await update.effective_chat.get_administrators()
            txt = "👑 <b>Admins:</b>\n\n"
            for a in admins:
                if not a.user.is_bot:
                    txt += f"• {mention(a.user)}\n"
            await q.message.reply_text(txt, parse_mode=ParseMode.HTML)
        except:
            await q.message.reply_text("❌ Could not fetch admin list.")
        return

# ─── HELP ──────────────────────────────────────────────────────────────────────
HELP = (
    "🤖 <b>Group Manager Bot — Commands</b>\n\n"
    "<b>🛡️ Moderation:</b>\n"
    "/ban · /unban · /kick\n"
    "/mute [10m/2h/1d] · /unmute\n"
    "/warn · /unwarn · /warns · /resetwarns\n\n"
    "<b>⚙️ Management:</b>\n"
    "/promote · /demote · /pin · /unpin\n"
    "/del · /purge · /setrules · /rules\n"
    "/setwelcome · /lock · /unlock\n\n"
    "<b>🔍 Auto-Tools:</b>\n"
    "/addbl · /delbl · /blacklist\n"
    "/setflood · /filter · /delfilter · /filters\n\n"
    "<b>📝 Notes:</b>\n"
    "/save · /get · /notes · /delnote\n\n"
    "<b>ℹ️ Info:</b>\n"
    "/adminlist · /chatinfo · /userinfo · /id\n"
    "/stats · /topusers · /banlist\n\n"
    "<b>🚨 Report:</b>\n"
    "Reply to a message + /report to report a user\n\n"
    "<b>👑 Owner Panel:</b>\n"
    "/panel — Access your group control panel\n\n"
    "<b>📢 Bot Owner:</b>\n"
    "/broadcast · /mygroups\n\n"
    "<i>💡 Tip: Reply to a message + command = easy targeting!</i>"
)

async def help_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(HELP, parse_mode=ParseMode.HTML)

# ─── HANDLE TEXT ───────────────────────────────────────────────────────────────
async def handle_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return
    chat_type = update.effective_chat.type
    if chat_type in ("group", "supergroup"):
        await check_blacklist(update, ctx)
        await check_filters(update, ctx)
        await check_flood(update, ctx)
        await count_message(update, ctx)

# ─── MAIN ──────────────────────────────────────────────────────────────────────
def main():
    if not BOT_TOKEN:
        logger.error("BOT_TOKEN not set!")
        return

    app = Application.builder().token(BOT_TOKEN).build()

    # Status handlers
    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, on_join))
    app.add_handler(MessageHandler(filters.StatusUpdate.LEFT_CHAT_MEMBER, on_leave))

    # Commands
    for cmd, fn in [
        ("start", start), ("help", help_cmd),
        ("ban", ban), ("unban", unban), ("kick", kick),
        ("mute", mute), ("unmute", unmute),
        ("warn", warn), ("unwarn", unwarn), ("warns", warns_cmd), ("resetwarns", resetwarns),
        ("promote", promote), ("demote", demote),
        ("pin", pin), ("unpin", unpin), ("del", delete_msg), ("purge", purge),
        ("setrules", setrules), ("rules", rules),
        ("setwelcome", setwelcome),
        ("addbl", addbl), ("delbl", delbl), ("blacklist", blacklist_cmd),
        ("setflood", setflood),
        ("lock", lock), ("unlock", unlock),
        ("save", save_note), ("get", get_note), ("notes", notes_cmd), ("delnote", delnote),
        ("filter", add_filter), ("delfilter", del_filter), ("filters", filters_cmd),
        ("adminlist", adminlist), ("chatinfo", chatinfo), ("userinfo", userinfo),
        ("id", get_id), ("stats", stats), ("topusers", topusers), ("banlist", banlist),
        ("report", report),
        ("panel", panel),
        ("broadcast", broadcast), ("mygroups", mygroups),
    ]:
        app.add_handler(CommandHandler(cmd, fn))

    # Callback handlers — order matters, most specific first
    app.add_handler(CallbackQueryHandler(report_cb, pattern="^rpt_"))
    app.add_handler(CallbackQueryHandler(group_panel_cb, pattern="^grppanel_|^gp_"))
    app.add_handler(CallbackQueryHandler(panel_cb, pattern="^show_|^show_my_panel$"))

    # Text handler (must be last)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    logger.info("🚀 Bot is running!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
