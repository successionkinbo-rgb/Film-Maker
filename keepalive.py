import os
import time
import json
import hmac
import hashlib
import logging
import threading

from urllib.parse import parse_qsl

import requests
from flask import Flask, request, jsonify, send_from_directory

import config
import database as db

log = logging.getLogger("server")
app = Flask(__name__, static_folder=None)

WEBAPP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "webapp")
TG_API = f"https://api.telegram.org/bot{config.BOT_TOKEN}"
_thumb_cache = {}  # video_id -> (content_type, bytes)


def invalidate_thumb_cache(video_id):
    """Call this any time a video's thumbnail_file_id changes (admin edit,
    auto-thumb on video replace, or delete) — otherwise /api/thumb/<id> keeps
    serving the old cached image bytes forever, since it never re-checks the
    DB once a video_id is cached."""
    _thumb_cache.pop(video_id, None)


# ================= keep-alive (unchanged) =================

@app.get("/")
def root():
    return "Bot is alive ✅"


@app.get("/health")
def health():
    return {"status": "ok", "ts": int(time.time())}


def _self_ping_loop(url: str, interval: int):
    while True:
        time.sleep(interval)
        try:
            requests.get(url, timeout=10)
            log.info("self-ping ok -> %s", url)
        except Exception as e:
            log.warning("self-ping failed: %s", e)


def start_keepalive():
    port = int(os.environ.get("PORT", "10000"))
    threading.Thread(
        target=lambda: app.run(host="0.0.0.0", port=port, use_reloader=False),
        daemon=True,
    ).start()
    external_url = os.environ.get("RENDER_EXTERNAL_URL") or os.environ.get("SELF_URL")
    if external_url:
        threading.Thread(
            target=_self_ping_loop,
            args=(external_url.rstrip("/") + "/health", 120),
            daemon=True,
        ).start()
        log.info("Self-ping enabled for %s every 120s", external_url)
    else:
        log.warning("RENDER_EXTERNAL_URL/SELF_URL not set — self-ping disabled.")


# ================= Telegram WebApp auth =================

def validate_init_data(init_data: str):
    """Verify Telegram WebApp initData signature; return the user dict or None.
    Also rejects stale initData (older than INIT_DATA_MAX_AGE) as a defence
    against a captured/leaked link being replayed later — note this cannot
    verify *which* Telegram client (mobile/desktop/web) originally opened the
    Mini App, since Telegram does not include that in the signed payload;
    the platform check on the client side is a UX gate, not a security one."""
    if not init_data:
        return None
    pairs = dict(parse_qsl(init_data, keep_blank_values=True))
    received_hash = pairs.pop("hash", None)
    if not received_hash:
        return None
    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
    secret_key = hmac.new(b"WebAppData", config.BOT_TOKEN.encode(), hashlib.sha256).digest()
    calc_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(calc_hash, received_hash):
        return None
    try:
        auth_date = int(pairs.get("auth_date", "0"))
        if auth_date <= 0 or (time.time() - auth_date) > config.INIT_DATA_MAX_AGE:
            return None
    except ValueError:
        return None
    try:
        return json.loads(pairs.get("user", "{}"))
    except Exception:
        return None


def require_user(raw_init):
    user = validate_init_data(raw_init)
    if not user or "id" not in user:
        return None
    return user["id"]


# ================= static app =================

@app.get("/app")
def app_index():
    return send_from_directory(WEBAPP_DIR, "index.html")


@app.get("/app/<path:filename>")
def app_static(filename):
    return send_from_directory(WEBAPP_DIR, filename)


# ================= API: browsing =================

@app.post("/api/whoami")
def api_whoami():
    """Lets the Mini App know (server-verified) whether the current viewer
    is an admin, so admin-only UI bypasses (screenshot deterrents, web-client
    block) can be applied client-side without trusting the client alone."""
    body = request.get_json(force=True)
    user_id = require_user(body.get("init", ""))
    if not user_id:
        return jsonify({"error": "invalid telegram session"}), 401
    return jsonify({"user_id": user_id, "is_admin": user_id in config.ADMIN_IDS})


@app.get("/api/categories")
def api_categories():
    cats = db.get_categories()
    return jsonify({"categories": ["all"] + cats})


@app.post("/api/videos")
def api_videos():
    body = request.get_json(force=True)
    category = body.get("category", "all")
    offset = int(body.get("offset", 0))
    rows = db.get_videos(category, offset, 8)
    total = db.count_videos(category)

    # Optional: if a valid Telegram session is attached, paint each card with
    # this user's 12h download-lock countdown (locked_remaining > 0 = locked).
    user_id = require_user(body.get("init", "")) if body.get("init") else None
    unlocks = db.bulk_get_unlocks(user_id, [r["id"] for r in rows]) if user_id else {}

    items = [{"id": r["id"], "title": r["title"], "category": r["category"],
              "added_at": r.get("added_at"), "thumb": f"/api/thumb/{r['id']}",
              "locked_remaining": db.locked_remaining_seconds(unlocks.get(r["id"]), config.UNLOCK_TTL_SECONDS)}
             for r in rows]
    return jsonify({"items": items, "total": total})


@app.post("/api/video/<int:video_id>")
def api_video(video_id):
    v = db.get_video(video_id)
    if not v:
        return jsonify({"error": "not found"}), 404
    body = request.get_json(silent=True) or {}
    user_id = require_user(body.get("init", ""))
    unlocked = False
    locked_remaining = 0
    if user_id:
        u = db.get_unlock(user_id, video_id)
        unlocked = db.is_still_unlocked(u, config.UNLOCK_TTL_SECONDS)
        locked_remaining = db.locked_remaining_seconds(u, config.UNLOCK_TTL_SECONDS)
    return jsonify({
        "id": v["id"], "title": v["title"], "category": v["category"],
        "category_label": config.category_label(v["category"]),
        "description": v.get("description", ""),
        "thumb": f"/api/thumb/{v['id']}", "unlocked": unlocked,
        "locked_remaining": locked_remaining,
    })


@app.get("/api/thumb/<int:video_id>")
def api_thumb(video_id):
    if video_id in _thumb_cache:
        ctype, blob = _thumb_cache[video_id]
        return app.response_class(blob, mimetype=ctype)
    v = db.get_video(video_id)
    if not v or not v["thumbnail_file_id"]:
        return "", 404
    try:
        info = requests.get(f"{TG_API}/getFile", params={"file_id": v["thumbnail_file_id"]}, timeout=10).json()
        file_path = info["result"]["file_path"]
        file_res = requests.get(f"https://api.telegram.org/file/bot{config.BOT_TOKEN}/{file_path}", timeout=15)
        ctype = file_res.headers.get("Content-Type", "image/jpeg")
        _thumb_cache[video_id] = (ctype, file_res.content)
        return app.response_class(file_res.content, mimetype=ctype)
    except Exception as e:
        log.warning("thumb fetch failed for %s: %s", video_id, e)
        return "", 404


def pick_direct_link(user_id, video_id, links, salt=""):
    """Deterministically spread traffic across the given direct ad links by
    (user, video) combination — same user+video always gets the same link
    (consistent if they retry), but different users and different videos land
    on different links, so revenue isn't concentrated on just one link. `salt`
    keeps Task 1's and Task 2's picks independent of each other."""
    h = hashlib.md5(f"{salt}:{user_id}:{video_id}".encode()).hexdigest()
    idx = int(h, 16) % len(links)
    return links[idx]


# ================= API: ad-unlock flow =================

@app.post("/api/ad/pending")
def api_ad_pending():
    """Called right before we call Monetag's show_XXX() for a task, so we can
    hand the SDK a ymid that only our server knows about. Monetag's postback
    for that ymid is the only thing that later marks the task server-verified."""
    body = request.get_json(force=True)
    user_id = require_user(body.get("init", ""))
    if not user_id:
        return jsonify({"error": "invalid telegram session"}), 401
    video_id = int(body["video_id"])
    step = int(body.get("step", 1))
    token = db.create_pending_reward(user_id, video_id, step)
    return jsonify({"ymid": token})


@app.get("/api/monetag/postback")
def api_monetag_postback():
    """Monetag calls this URL server-to-server (GET, with macros filled in)
    once an ad event is confirmed on their backend — this request never
    passes through the user's device, so it can't be faked by tampering with
    the Mini App's JS. Configure this exact URL (with macros) in the Monetag
    SSP under your zone's Postback URL setting — see /tutoriallink-style note
    in the deploy docs for the full URL to paste."""
    ymid = request.args.get("ymid", "")
    reward_event_type = request.args.get("value", "")  # "valued" or "non_valued"
    doc = db.consume_pending_reward(ymid) if ymid else None
    if doc and reward_event_type == "valued":
        db.mark_ad_verified(doc["user_id"], doc["video_id"], doc["step"])
        log.info("Monetag postback verified: user=%s video=%s step=%s",
                  doc["user_id"], doc["video_id"], doc["step"])
    else:
        log.info("Monetag postback ignored (ymid=%s, value=%s)", ymid, reward_event_type)
    return "", 200  # always 200 so Monetag doesn't retry a dead attempt


@app.post("/api/unlock/start")
def api_unlock_start():
    body = request.get_json(force=True)
    user_id = require_user(body.get("init", ""))
    if not user_id:
        return jsonify({"error": "invalid telegram session"}), 401
    video_id = int(body["video_id"])
    video = db.get_video(video_id)
    if not video:
        return jsonify({"error": "video not found"}), 404

    existing = db.get_unlock(user_id, video_id)
    if db.is_still_unlocked(existing, config.UNLOCK_TTL_SECONDS):
        remaining = db.locked_remaining_seconds(existing, config.UNLOCK_TTL_SECONDS)
        return jsonify({"error": "locked", "locked_remaining": remaining}), 400

    db.start_unlock(user_id, video_id)
    ad_link = pick_direct_link(user_id, video_id, config.TASK1_AD_LINKS, salt="task1")
    db.log_ad_event(user_id, video_id, 1, "shown", ad_link)
    return jsonify({"wait_seconds": config.AD_WAIT_SECONDS, "ad_link": ad_link})


@app.post("/api/unlock/ad1")
def api_unlock_ad1():
    body = request.get_json(force=True)
    user_id = require_user(body.get("init", ""))
    if not user_id:
        return jsonify({"error": "invalid telegram session"}), 401
    video_id = int(body["video_id"])
    unlock = db.get_unlock(user_id, video_id)
    if not unlock or not unlock["ad1_shown_at"]:
        return jsonify({"error": "start unlock first"}), 400
    elapsed = int(time.time()) - unlock["ad1_shown_at"]
    if not unlock.get("ad1_verified") and elapsed < config.AD_WAIT_SECONDS:
        return jsonify({"error": f"wait {config.AD_WAIT_SECONDS - elapsed}s more"}), 400

    db.mark_ad1_clicked(user_id, video_id)
    db.log_ad_event(user_id, video_id, 1, "clicked", "")
    ad_link_2 = pick_direct_link(user_id, video_id, config.TASK2_AD_LINKS, salt="task2")
    db.log_ad_event(user_id, video_id, 2, "shown", ad_link_2)
    return jsonify({"wait_seconds": config.AD_WAIT_SECONDS, "ad_link": ad_link_2})


@app.post("/api/unlock/ad2")
def api_unlock_ad2():
    body = request.get_json(force=True)
    user_id = require_user(body.get("init", ""))
    if not user_id:
        return jsonify({"error": "invalid telegram session"}), 401
    video_id = int(body["video_id"])
    unlock = db.get_unlock(user_id, video_id)
    if not unlock or not unlock["ad1_clicked_at"] or not unlock["ad2_shown_at"]:
        return jsonify({"error": "complete task 1 first"}), 400
    elapsed = int(time.time()) - unlock["ad2_shown_at"]
    if not unlock.get("ad2_verified") and elapsed < config.AD_WAIT_SECONDS:
        return jsonify({"error": f"wait {config.AD_WAIT_SECONDS - elapsed}s more"}), 400

    db.mark_unlocked(user_id, video_id)
    db.log_ad_event(user_id, video_id, 2, "clicked", "")
    video = db.get_video(video_id)
    db.add_history(user_id, video_id)

    try:
        requests.post(f"{TG_API}/copyMessage", json={
            "chat_id": user_id,
            "from_chat_id": video["storage_chat_id"],
            "message_id": video["storage_message_id"],
            "caption": f"🎬 {video['title']}",
            "protect_content": True,
        }, timeout=15)
    except Exception as e:
        log.warning("video delivery failed for user %s: %s", user_id, e)
        return jsonify({"error": "delivery failed, try again"}), 500

    return jsonify({"success": True})


@app.post("/api/history")
def api_history():
    body = request.get_json(force=True)
    user_id = require_user(body.get("init", ""))
    if not user_id:
        return jsonify({"error": "invalid telegram session"}), 401
    rows = db.get_user_history(user_id)
    items = [{
        "title": r["title"],
        "category_label": config.category_label(r["category"]),
        "when": time.strftime("%d %b, %H:%M", time.localtime(r["watched_at"])),
    } for r in rows]
    return jsonify({"items": items})
