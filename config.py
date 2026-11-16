import os

BOT_TOKEN = os.environ.get("BOT_TOKEN", "PUT_YOUR_BOT_TOKEN_HERE")
MONGODB_URI = os.environ.get("MONGODB_URI", "").strip()


# Comma-separated Telegram user IDs allowed to use the admin panel
ADMIN_IDS = {
    int(x) for x in os.environ.get("ADMIN_IDS", "").replace(" ", "").split(",") if x
}

# Private channel/group where the bot (added as admin) stores the actual
# video files so the media itself is never public. The bot copies the file
# here on upload and re-sends it to users from here on unlock.
# Use the channel's numeric id, e.g. -1001234567890
STORAGE_CHAT_ID = int(os.environ.get("STORAGE_CHAT_ID", "0"))

AD_WAIT_SECONDS = int(os.environ.get("AD_WAIT_SECONDS", "15"))
# Both tasks are now direct ad links, each rotated per (user, video)
# combination so traffic spreads across all 4 for the best blended revenue
# rather than everyone hitting the same one. The two tasks rotate
# independently (different salt), so the same user+video doesn't necessarily
# land on the "matching" link index for both tasks.
TASK1_AD_LINKS = [
    "https://omg10.com/4/11903448",
    "https://omg10.com/4/11903442",
    "https://omg10.com/4/11903389",
    "https://omg10.com/4/11903443",
]
TASK2_AD_LINKS = [
    "https://www.profitableratecpmnetwork.com/m46hsu0sau?key=3b1bd3ee288ce3133c8d2cfd128b6dab",
    "https://www.profitableratecpmnetwork.com/z1k6iwpp0f?key=035a41f483a78e2b618a5fb4b833a20c",
    "https://www.profitableratecpmnetwork.com/wtg129p0m1?key=0d5ce0347cfa5d34808d2a09ed6a8a0f",
    "https://www.profitableratecpmnetwork.com/zxqbtdpgt?key=f05f9d4968d095723c7b61b3fddf2870",
]
DIRECT_AD_LINKS_2 = [
    "https://www.profitableratecpmnetwork.com/m46hsu0sau?key=3b1bd3ee288ce3133c8d2cfd128b6dab",
    "https://www.profitableratecpmnetwork.com/z1k6iwpp0f?key=035a41f483a78e2b618a5fb4b833a20c",
    "https://www.profitableratecpmnetwork.com/wtg129p0m1?key=0d5ce0347cfa5d34808d2a09ed6a8a0f",
    "https://www.profitableratecpmnetwork.com/zxqbtdpgt?key=f05f9d4968d095723c7b61b3fddf2870",
]
# After a user completes both ad tasks and downloads a video, that specific
# video becomes LOCKED (re-download blocked) for this many seconds. Once it
# expires, the video auto re-unlocks and the ad flow is required again to
# download it. Default: 12 hours.
UNLOCK_TTL_SECONDS = int(os.environ.get("UNLOCK_TTL_SECONDS", str(12 * 60 * 60)))
INIT_DATA_MAX_AGE = int(os.environ.get("INIT_DATA_MAX_AGE", "86400"))  # 24h replay window

CATEGORIES = {
    "movie": "🎬 Movie",
    "natok": "📺 Natok",
    "series": "🎞️ Series",
}

_CATEGORY_EMOJIS = {"all": "🗂️", "movie": "🎬", "natok": "📺", "series": "🎞️", "drama": "🎭"}


def category_label(cat: str) -> str:
    """Best-effort emoji + label for any (possibly custom, admin-typed) category name."""
    emoji = _CATEGORY_EMOJIS.get(cat.strip().lower(), "📁")
    return f"{emoji} {cat}"


def get_webapp_url():
    """Public HTTPS URL of the Mini App. Manual WEBAPP_URL env wins; otherwise
    derived from Render's auto-set RENDER_EXTERNAL_URL. WebApp buttons need
    HTTPS, so this returns None (and the button is hidden) when neither is set."""
    manual = os.environ.get("WEBAPP_URL")
    if manual:
        return manual.rstrip("/")
    base = os.environ.get("RENDER_EXTERNAL_URL")
    if base:
        return base.rstrip("/") + "/app"
    return None

