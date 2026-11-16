import os

BOT_TOKEN = os.environ.get("BOT_TOKEN", "PUT_YOUR_BOT_TOKEN_HERE")
MONGODB_URI = os.environ.get("MONGODB_URI", "").strip()


# Comma-separated Telegram user IDs allowed to use the admin panel
ADMIN_IDS = {
    int(x) for x in os.environ.get("ADMIN_IDS", "8762983192").replace(" ", "").split(",") if x
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
    "https://auctionr.org/4/1b46c719a87f7cf5f18ae59d8aa2a2f1",
    "https://auctionr.org/4/4783679bb3f92df146f36ac8c524389c",
    "https://auctionr.org/4/704548d464ffd9cdc3d20a21f95d56f4",
]
TASK2_AD_LINKS = [
    "https://auctionr.org/4/1b46c719a87f7cf5f18ae59d8aa2a2f1",
    "https://auctionr.org/4/4783679bb3f92df146f36ac8c524389c",
    "https://auctionr.org/4/704548d464ffd9cdc3d20a21f95d56f4",
]
DIRECT_AD_LINKS_2 = [
    "https://auctionr.org/4/1b46c719a87f7cf5f18ae59d8aa2a2f1",
    "https://auctionr.org/4/4783679bb3f92df146f36ac8c524389c",
    "https://auctionr.org/4/704548d464ffd9cdc3d20a21f95d56f4",
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

