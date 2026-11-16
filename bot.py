import asyncio
import logging
import os
import time

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    WebAppInfo,
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)

from telegram.error import Conflict, NetworkError

import config
import database as db
from keepalive import start_keepalive, invalidate_thumb_cache

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

PAGE_SIZE = 8

# ---------- Admin conversation states ----------
(ADD_TITLE, ADD_CATEGORY, ADD_DESC, ADD_THUMB, ADD_VIDEO) = range(5)
(EDIT_MENU, EDIT_TITLE, EDIT_CATEGORY, EDIT_DESC, EDIT_THUMB, EDIT_VIDEO) = range(10, 16)


WELCOME_IMAGE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "welcome.png")


def is_admin(user_id):
    return user_id in config.ADMIN_IDS


def _fmt_hms(total_seconds):
    total_seconds = max(0, int(total_seconds))
    h, rem = divmod(total_seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h {m}m"
    if m:
        return f"{m}m {s}s"
    return f"{s}s"


# ================= USER SIDE =================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    db.upsert_user(user.id, user.username or user.first_name)
    webapp_url = config.get_webapp_url()

    if webapp_url:
        # Everything (browse, unlock, history) lives inside the Mini App now —
        # the chat message is just a short description + launch buttons.
        text = (
            f"👋 Hi {user.first_name}!\n\n"
            "🎬 <b>Cine Hub</b> — Movie, Natok, Series, Drama সব একসাথে, "
            "premium app interface-এ দেখুন।\n\n"
            "নিচের 🎬 Watch Now বাটনে চাপ দিয়ে Video Download করুন📱"
        )
        buttons = [[InlineKeyboardButton("🎬 Watch Now", web_app=WebAppInfo(url=webapp_url))]]
        tutorial_link = db.get_setting("tutorial_link")
        if tutorial_link:
            buttons.append([InlineKeyboardButton("Tutorial", url=tutorial_link)])
        if is_admin(user.id):
            text += (
                "\n\n⚙️ <b>Admin:</b> /addvideo · /editvideo &lt;id&gt; · /listvideos · /delvideo &lt;id&gt; "
                "· /adstats · /tutoriallink &lt;link&gt;"
            )
        markup = InlineKeyboardMarkup(buttons)
        if update.message:
            try:
                with open(WELCOME_IMAGE_PATH, "rb") as photo:
                    await update.message.reply_photo(
                        photo=photo, caption=text, parse_mode=ParseMode.HTML,
                        reply_markup=markup, protect_content=True,
                    )
            except Exception:
                # Never let a photo-send hiccup (network blip, Telegram media
                # error, missing file, etc.) mean the user gets NO response at
                # all — always fall back to a plain text welcome.
                log.exception("start(): welcome photo failed for user %s, falling back to text", user.id)
                await update.message.reply_text(
                    text, parse_mode=ParseMode.HTML, reply_markup=markup, protect_content=True,
                )
        else:
            # Coming back to /start via an inline button — the message is
            # already our photo message, so edit its caption in place.
            await update.callback_query.edit_message_caption(
                caption=text, parse_mode=ParseMode.HTML, reply_markup=markup,
            )
        return

    # Fallback (no WEBAPP_URL configured yet) — old in-chat button flow, dynamic categories
    cats = db.get_categories()
    buttons = [[InlineKeyboardButton(config.category_label("all"), callback_data="cat:all:0")]]
    buttons += [[InlineKeyboardButton(config.category_label(c), callback_data=f"cat:{c}:0")] for c in cats]
    buttons.append([InlineKeyboardButton("🕘 My History", callback_data="history")])
    if is_admin(user.id):
        buttons.append([InlineKeyboardButton("⚙️ Admin Panel", callback_data="admin_menu")])
    text = (
        f"👋 Hi {user.first_name}!\n\n"
        "Netflix-style bot e apnake shagotom. Niche theke category বেছে nin. 🎬"
    )
    markup = InlineKeyboardMarkup(buttons)
    if update.message:
        try:
            with open(WELCOME_IMAGE_PATH, "rb") as photo:
                await update.message.reply_photo(photo=photo, caption=text, reply_markup=markup, protect_content=True)
        except Exception:
            log.exception("start(): welcome photo failed for user %s, falling back to text", user.id)
            await update.message.reply_text(text, reply_markup=markup, protect_content=True)
    else:
        await update.callback_query.edit_message_caption(caption=text, reply_markup=markup)


async def show_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    _, category, offset = query.data.split(":")
    offset = int(offset)

    videos = db.get_videos(category, offset, PAGE_SIZE)
    total = db.count_videos(category)

    if not videos:
        await query.edit_message_text(
            f"{config.category_label(category)} e ekhono kono video nei.",
            reply_markup=back_to_menu_kb(),
        )
        return

    rows = [[InlineKeyboardButton(f"▶️ {v['title']}", callback_data=f"video:{v['id']}")] for v in videos]

    nav = []
    if offset > 0:
        nav.append(InlineKeyboardButton("⬅️ Age", callback_data=f"cat:{category}:{max(0, offset - PAGE_SIZE)}"))
    if offset + PAGE_SIZE < total:
        nav.append(InlineKeyboardButton("Pore ➡️", callback_data=f"cat:{category}:{offset + PAGE_SIZE}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton("🏠 Menu", callback_data="menu")])

    await query.edit_message_text(
        f"{config.category_label(category)} — list:",
        reply_markup=InlineKeyboardMarkup(rows),
    )


async def show_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    video_id = int(query.data.split(":")[1])
    video = db.get_video(video_id)
    if not video:
        await query.edit_message_text("Video ta ar available nei.", reply_markup=back_to_menu_kb())
        return

    unlock = db.get_unlock(query.from_user.id, video_id)
    is_locked = db.is_still_unlocked(unlock, config.UNLOCK_TTL_SECONDS)
    remaining = db.locked_remaining_seconds(unlock, config.UNLOCK_TTL_SECONDS)

    caption = f"🎬 <b>{video['title']}</b>\nCategory: {config.category_label(video['category'])}\n\n"
    caption += (f"🔒 Ei video ta apni already download korechen — abar download korte {_fmt_hms(remaining)} wait korun."
                if is_locked else "Video dekhte hole 2ta ad task complete korte hobe (protita 15 sec wait).")

    kb = [[InlineKeyboardButton(
        f"🔒 Locked ({_fmt_hms(remaining)})" if is_locked else "🔓 Unlock & Watch",
        callback_data=f"watch:{video_id}",
    )], [InlineKeyboardButton("🔙 Back", callback_data=f"cat:{video['category']}:0")]]

    if video["thumbnail_file_id"]:
        await query.message.reply_photo(
            video["thumbnail_file_id"], caption=caption,
            parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(kb),
        )
        await query.delete_message()
    else:
        await query.edit_message_text(caption, parse_mode=ParseMode.HTML,
                                       reply_markup=InlineKeyboardMarkup(kb))


async def watch(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    video_id = int(query.data.split(":")[1])
    user_id = query.from_user.id
    video = db.get_video(video_id)
    if not video:
        await query.answer("Video pawa jayni.", show_alert=True)
        return

    unlock = db.get_unlock(user_id, video_id)
    if db.is_still_unlocked(unlock, config.UNLOCK_TTL_SECONDS):
        remaining = db.locked_remaining_seconds(unlock, config.UNLOCK_TTL_SECONDS)
        await query.answer(f"🔒 Locked — aro {_fmt_hms(remaining)} pore abar download korte parben.", show_alert=True)
        return

    # Ad verification (open link + confirm real time-away) only runs inside
    # the Mini App's browser context (JS) — plain Telegram chat buttons can't
    # do that. This in-chat flow only ever runs when WEBAPP_URL isn't
    # configured, so just point the admin/user at fixing that instead of a
    # broken ad step here.
    await query.answer(
        "⚠️ Ad-verified download শুধু Mini App-এ কাজ করে। WEBAPP_URL env var সেট করে bot redeploy করুন, "
        "তারপর /start থেকে 'Watch Now' চাপুন।", show_alert=True,
    )


async def ad2_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    video_id = int(query.data.split(":")[1])
    user_id = query.from_user.id
    unlock = db.get_unlock(user_id, video_id)
    if not unlock or not unlock["ad1_clicked_at"] or not unlock["ad2_shown_at"]:
        await query.answer("Age Task 1 complete korun.", show_alert=True)
        return

    elapsed = int(time.time()) - unlock["ad2_shown_at"]
    if elapsed < config.AD_WAIT_SECONDS:
        await query.answer(f"Aro {config.AD_WAIT_SECONDS - elapsed} second wait korun ⏳", show_alert=True)
        return

    db.mark_unlocked(user_id, video_id)
    db.log_ad_event(user_id, video_id, 2, "clicked", "")
    await query.answer("🎉 Unlocked!")
    video = db.get_video(video_id)
    await query.message.edit_text("✅ Duita task complete! Video pathano hocche...")
    await deliver_video(update, context, video, user_id)


async def deliver_video(update, context, video, user_id):
    db.add_history(user_id, video["id"])
    try:
        await context.bot.copy_message(
            chat_id=user_id,
            from_chat_id=video["storage_chat_id"],
            message_id=video["storage_message_id"],
            caption=f"🎬 {video['title']}",
            protect_content=True,
        )
    except Exception as e:
        log.exception("delivery failed")
        await context.bot.send_message(user_id, f"⚠️ Video pathate somossa hoyeche: {e}")


async def history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    rows = db.get_user_history(query.from_user.id)
    if not rows:
        text = "Apnar chart ekhono khali. Kono video dekhen ni."
    else:
        lines = ["🕘 <b>Apnar Watch History</b>\n"]
        for r in rows:
            t = time.strftime("%d %b, %H:%M", time.localtime(r["watched_at"]))
            lines.append(f"• {r['title']} ({config.category_label(r['category'])}) — {t}")
        text = "\n".join(lines)
    await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=back_to_menu_kb())


async def menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start(update, context)


def back_to_menu_kb():
    return InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Menu", callback_data="menu")]])


# ================= ADMIN PANEL =================

async def admin_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_admin(query.from_user.id):
        await query.answer("Apni admin na.", show_alert=True)
        return
    text = (
        "⚙️ <b>Admin Panel</b>\n\n"
        "/addvideo — Notun video thumbnail soho upload korun\n"
        "/listvideos — Shob video dekhun\n"
        "/delvideo <id> — Video delete korun\n"
        "/cancel — Chalu process বাতিল"
    )
    await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=back_to_menu_kb())


async def addvideo_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
    context.user_data["new_video"] = {}
    await update.message.reply_text("📝 Video/Movie er Title pathan:")
    return ADD_TITLE


async def addvideo_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["new_video"]["title"] = update.message.text.strip()
    existing = db.get_categories()
    hint = ("\n\nExisting categories: " + ", ".join(existing)) if existing else ""
    await update.message.reply_text(
        "Category naam type korun (notun ba purono jekono — jemon Movie, Drama, "
        "Bachelor S5, ইত্যাদি)।" + hint
    )
    return ADD_CATEGORY


async def addvideo_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    category = update.message.text.strip()
    if not category:
        await update.message.reply_text("Khali category dile hobe na, abar type korun:")
        return ADD_CATEGORY
    context.user_data["new_video"]["category"] = category
    await update.message.reply_text(
        f"Category set: {config.category_label(category)}\n\n"
        "📝 Description din (na dile 'skip' type korun):"
    )
    return ADD_DESC


async def addvideo_desc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    context.user_data["new_video"]["description"] = "" if text.lower() in ("skip", "-") else text
    await update.message.reply_text(
        "Ekhon thumbnail image পাঠান (YouTube-style wide/16:9 image best), "
        "othoba 'skip' likhun — tahole video-r nijer auto-thumbnail use hobe:"
    )
    return ADD_THUMB


async def addvideo_thumb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text and update.message.text.strip().lower() in ("skip", "-"):
        context.user_data["new_video"]["thumbnail_file_id"] = None
        await update.message.reply_text(
            "Thumbnail skip holo — video upload korar somoy Telegram-er own thumbnail "
            "automatic use hobe.\n\n🎥 Ekhon video file ta upload/forward korun (document/video hisebe):"
        )
        return ADD_VIDEO
    if not update.message.photo:
        await update.message.reply_text("Ekta image (photo) pathan thumbnail hisebe, ba 'skip' likhun.")
        return ADD_THUMB
    context.user_data["new_video"]["thumbnail_file_id"] = update.message.photo[-1].file_id
    await update.message.reply_text("🎥 Ekhon video file ta upload/forward korun (document/video hisebe):")
    return ADD_VIDEO


def _extract_auto_thumb(message):
    """Telegram auto-generates a thumbnail for uploaded video/document files —
    reuse that file_id when the admin skipped a manual thumbnail."""
    obj = message.video or message.document
    thumb = getattr(obj, "thumbnail", None) if obj else None
    return thumb.file_id if thumb else None


async def addvideo_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not (update.message.video or update.message.document):
        await update.message.reply_text("Ekta video file pathan.")
        return ADD_VIDEO

    if not config.STORAGE_CHAT_ID:
        await update.message.reply_text(
            "⚠️ STORAGE_CHAT_ID set kora nei config e. Ekta private channel toiri kore "
            "bot ke admin banan, tarpor .env e channel id ta din."
        )
        return ConversationHandler.END

    nv = context.user_data["new_video"]
    video_id = db.reserve_video_id()

    # copy the file into the private storage group so media stays private —
    # caption carries the video ID so admin can identify it there later
    # (for editing/deleting), and protect_content stops anyone in that group
    # from forwarding/saving it further.
    copied = await context.bot.copy_message(
        chat_id=config.STORAGE_CHAT_ID,
        from_chat_id=update.effective_chat.id,
        message_id=update.message.message_id,
        caption=f"🎬 Video ID: {video_id}\n{nv['title']}",
        protect_content=True,
    )

    thumb_id = nv.get("thumbnail_file_id") or _extract_auto_thumb(update.message)
    db.insert_video(
        video_id,
        title=nv["title"],
        category=nv["category"],
        description=nv.get("description", ""),
        thumbnail_file_id=thumb_id,
        storage_chat_id=config.STORAGE_CHAT_ID,
        storage_message_id=copied.message_id,
        added_by=update.effective_user.id,
    )
    note = "" if thumb_id else "\n⚠️ Auto-thumbnail o paoa jayni — pore /editvideo diye add korte parben."
    await update.message.reply_text(f"✅ '{nv['title']}' save hoyeche! (ID: {video_id}){note}")
    context.user_data.pop("new_video", None)
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("new_video", None)
    await update.message.reply_text("❌ Process বাতিল করা হলো।")
    return ConversationHandler.END


# ================= EDIT VIDEO =================

EDIT_FIELD_LABELS = {
    "title": "📝 Title", "category": "🏷️ Category", "description": "🧾 Description",
    "thumbnail_file_id": "🖼️ Thumbnail", "video": "🎥 Video File",
}


def _edit_menu_kb():
    rows = [
        [InlineKeyboardButton(EDIT_FIELD_LABELS["title"], callback_data="editfield:title"),
         InlineKeyboardButton(EDIT_FIELD_LABELS["category"], callback_data="editfield:category")],
        [InlineKeyboardButton(EDIT_FIELD_LABELS["description"], callback_data="editfield:description"),
         InlineKeyboardButton(EDIT_FIELD_LABELS["thumbnail_file_id"], callback_data="editfield:thumbnail_file_id")],
        [InlineKeyboardButton(EDIT_FIELD_LABELS["video"], callback_data="editfield:video")],
        [InlineKeyboardButton("✅ Done", callback_data="editdone")],
    ]
    return InlineKeyboardMarkup(rows)


def _edit_summary_text(v):
    return (
        f"✏️ <b>Editing:</b> {v['title']} (ID {v['id']})\n"
        f"Category: {config.category_label(v['category'])}\n"
        f"Description: {v.get('description') or '—'}\n\n"
        "Kon field change korte chan?"
    )


async def editvideo_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
    if not context.args:
        await update.message.reply_text("Usage: /editvideo <id>")
        return ConversationHandler.END
    video_id = int(context.args[0])
    v = db.get_video(video_id)
    if not v:
        await update.message.reply_text("Ei ID-r video pawa jayni.")
        return ConversationHandler.END
    context.user_data["edit_video_id"] = video_id
    await update.message.reply_text(_edit_summary_text(v), parse_mode=ParseMode.HTML, reply_markup=_edit_menu_kb())
    return EDIT_MENU


async def _show_edit_menu(message, video_id, prefix=""):
    v = db.get_video(video_id)
    text = (prefix + "\n\n" if prefix else "") + _edit_summary_text(v)
    await message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=_edit_menu_kb())


async def editfield_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    field = query.data.split(":")[1]
    context.user_data["edit_field"] = field
    prompts = {
        "title": ("📝 Notun title din:", EDIT_TITLE),
        "category": ("🏷️ Notun category din:", EDIT_CATEGORY),
        "description": ("🧾 Notun description din ('skip' likhle khali thakbe):", EDIT_DESC),
        "thumbnail_file_id": ("🖼️ Notun thumbnail image পাঠান:", EDIT_THUMB),
        "video": ("🎥 Notun video file পাঠান (age-r video replace hobe):", EDIT_VIDEO),
    }
    text, state = prompts[field]
    await query.message.reply_text(text)
    return state


async def editvideo_save_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    field = context.user_data["edit_field"]
    video_id = context.user_data["edit_video_id"]
    value = update.message.text.strip()
    if field == "description" and value.lower() in ("skip", "-"):
        value = ""
    db.update_video(video_id, **{field: value})
    await _show_edit_menu(update.message, video_id, "✅ Update হয়েছে!")
    return EDIT_MENU


async def editvideo_save_thumb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.photo:
        await update.message.reply_text("Ekta image pathan.")
        return EDIT_THUMB
    video_id = context.user_data["edit_video_id"]
    db.update_video(video_id, thumbnail_file_id=update.message.photo[-1].file_id)
    invalidate_thumb_cache(video_id)
    await _show_edit_menu(update.message, video_id, "✅ Thumbnail update হয়েছে!")
    return EDIT_MENU


async def editvideo_save_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not (update.message.video or update.message.document):
        await update.message.reply_text("Ekta video file pathan.")
        return EDIT_VIDEO
    video_id = context.user_data["edit_video_id"]
    v = db.get_video(video_id)
    copied = await context.bot.copy_message(
        chat_id=config.STORAGE_CHAT_ID,
        from_chat_id=update.effective_chat.id,
        message_id=update.message.message_id,
        caption=f"🎬 Video ID: {video_id}\n{v['title']}",
        protect_content=True,
    )
    updates = {"storage_chat_id": config.STORAGE_CHAT_ID, "storage_message_id": copied.message_id}
    if not v.get("thumbnail_file_id"):
        auto_thumb = _extract_auto_thumb(update.message)
        if auto_thumb:
            updates["thumbnail_file_id"] = auto_thumb
    db.update_video(video_id, **updates)
    if "thumbnail_file_id" in updates:
        invalidate_thumb_cache(video_id)
    await _show_edit_menu(update.message, video_id, "✅ Video file replace হয়েছে!")
    return EDIT_MENU


async def editvideo_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data.pop("edit_video_id", None)
    context.user_data.pop("edit_field", None)
    await query.edit_message_text("✅ Edit শেষ।")
    return ConversationHandler.END


async def listvideos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    rows = db.list_all_videos()
    if not rows:
        await update.message.reply_text("Kono video nei.")
        return
    lines = [f"#{r['id']} [{r['category']}] {r['title']}" for r in rows]
    await update.message.reply_text("\n".join(lines))


async def adstats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Simple in-house ad impression/click tracker (per video, per ad slot)."""
    if not is_admin(update.effective_user.id):
        return
    video_id = int(context.args[0]) if context.args else None
    rows = db.ad_stats(video_id)
    if not rows:
        await update.message.reply_text("Kono ad event log hoyni ekhono.")
        return
    lines = ["📊 <b>Ad Tracker</b>"]
    for r in rows:
        lines.append(f"Video #{r['video_id']} · Ad{r['ad_number']} · {r['event']}: {r['c']}")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


async def tutoriallink(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin-only: view or set the link the /start message's 'Tutorial'
    button opens (a Telegram group's tutorial video/message link). Takes
    effect on the very next /start — no code change or redeploy needed."""
    if not is_admin(update.effective_user.id):
        return
    if not context.args:
        current = db.get_setting("tutorial_link")
        await update.message.reply_text(
            f"🔗 Current Tutorial link:\n{current or '(not set — Tutorial button is hidden)'}\n\n"
            "Set/change it with:\n/tutoriallink <link>"
        )
        return
    link = context.args[0].strip()
    db.set_setting("tutorial_link", link)
    await update.message.reply_text(f"✅ Tutorial link updated:\n{link}")


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    """Global fallback: logs any exception that escapes a handler, so a
    handler failing for one user (while working for another) shows up
    clearly in the logs instead of just... not responding."""
    log.error("Unhandled exception while processing update %s", update, exc_info=context.error)


async def chatid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Utility: reply with the current chat's numeric ID — handy for setting
    STORAGE_CHAT_ID from inside a private group/channel where the bot is a member.
    Works even while the bot is polling (unlike manually hitting getUpdates,
    which returns [] because the running bot already consumed the update)."""
    if not is_admin(update.effective_user.id):
        return
    chat = update.effective_chat
    await update.message.reply_text(
        f"📌 Chat: {chat.title or chat.type}\n🆔 ID: `{chat.id}`",
        parse_mode=ParseMode.MARKDOWN,
    )


async def delvideo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    if not context.args:
        await update.message.reply_text("Usage: /delvideo <id>")
        return
    vid = int(context.args[0])
    db.delete_video(vid)
    invalidate_thumb_cache(vid)
    await update.message.reply_text("🗑️ Delete kora hoyeche.")


# ================= MAIN =================

def main():
    db.init_db()
    start_keepalive()
    # explicit event loop for the main thread — newer Python (3.12+/3.14)
    # no longer auto-creates one, which python-telegram-bot's run_polling relies on
    asyncio.set_event_loop(asyncio.new_event_loop())
    app = ApplicationBuilder().token(config.BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("listvideos", listvideos))
    app.add_handler(CommandHandler("delvideo", delvideo))
    app.add_handler(CommandHandler("adstats", adstats))
    app.add_handler(CommandHandler("chatid", chatid))
    app.add_handler(CommandHandler("tutoriallink", tutoriallink))

    app.add_handler(CallbackQueryHandler(menu_callback, pattern="^menu$"))
    app.add_handler(CallbackQueryHandler(show_category, pattern="^cat:"))
    app.add_handler(CallbackQueryHandler(show_video, pattern="^video:"))
    app.add_handler(CallbackQueryHandler(watch, pattern="^watch:"))
    app.add_handler(CallbackQueryHandler(ad2_done, pattern="^ad2:"))
    app.add_handler(CallbackQueryHandler(history, pattern="^history$"))
    app.add_handler(CallbackQueryHandler(admin_menu, pattern="^admin_menu$"))

    conv = ConversationHandler(
        entry_points=[CommandHandler("addvideo", addvideo_start)],
        states={
            ADD_TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, addvideo_title)],
            ADD_CATEGORY: [MessageHandler(filters.TEXT & ~filters.COMMAND, addvideo_category)],
            ADD_DESC: [MessageHandler(filters.TEXT & ~filters.COMMAND, addvideo_desc)],
            ADD_THUMB: [MessageHandler(filters.PHOTO | (filters.TEXT & ~filters.COMMAND), addvideo_thumb)],
            ADD_VIDEO: [MessageHandler(filters.VIDEO | filters.Document.VIDEO, addvideo_video)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    app.add_handler(conv)

    edit_conv = ConversationHandler(
        entry_points=[CommandHandler("editvideo", editvideo_start)],
        states={
            EDIT_MENU: [CallbackQueryHandler(editfield_choice, pattern="^editfield:"),
                        CallbackQueryHandler(editvideo_done, pattern="^editdone$")],
            EDIT_TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, editvideo_save_text)],
            EDIT_CATEGORY: [MessageHandler(filters.TEXT & ~filters.COMMAND, editvideo_save_text)],
            EDIT_DESC: [MessageHandler(filters.TEXT & ~filters.COMMAND, editvideo_save_text)],
            EDIT_THUMB: [MessageHandler(filters.PHOTO, editvideo_save_thumb)],
            EDIT_VIDEO: [MessageHandler(filters.VIDEO | filters.Document.VIDEO, editvideo_save_video)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    app.add_handler(edit_conv)
    app.add_error_handler(on_error)

    # Retry loop: if getUpdates hits a Conflict (another instance briefly
    # still polling during a redeploy) or a transient network error, wait
    # and retry instead of letting the whole process crash and exit(1).
    retry_delay = 5
    while True:
        try:
            log.info("Bot starting (polling)...")
            app.run_polling(drop_pending_updates=True, close_loop=False)
            break  # run_polling returned normally (clean shutdown)
        except Conflict:
            log.warning(
                "Conflict: another instance is polling with this token. "
                "Retrying in %ss — if this repeats, check for a duplicate "
                "Render service or a local `python bot.py` still running.",
                retry_delay,
            )
            time.sleep(retry_delay)
        except NetworkError as e:
            log.warning("Network error (%s), retrying in %ss", e, retry_delay)
            time.sleep(retry_delay)


if __name__ == "__main__":
    main()
