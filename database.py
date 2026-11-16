import json
import os
import threading
import time
import logging
import secrets

from pymongo import MongoClient, ReturnDocument, DESCENDING

import config

log = logging.getLogger("database")


class _LocalCursor:
    def __init__(self, docs):
        self._docs = list(docs)

    def sort(self, key_or_list, direction=None):
        if isinstance(key_or_list, list):
            for k, d in reversed(key_or_list):
                rev = (d == DESCENDING or d == -1)
                self._docs.sort(key=lambda x: x.get(k, 0) or 0, reverse=rev)
        else:
            rev = (direction == DESCENDING or direction == -1)
            self._docs.sort(key=lambda x: x.get(key_or_list, 0) or 0, reverse=rev)
        return self

    def skip(self, n):
        self._docs = self._docs[n:]
        return self

    def limit(self, n):
        self._docs = self._docs[:n]
        return self

    def __iter__(self):
        return iter(self._docs)


class _LocalCollection:
    _lock = threading.RLock()

    def __init__(self, name, storage_path):
        self.name = name
        self.storage_path = storage_path
        self._data = []
        self._load()

    def _load(self):
        if os.path.exists(self.storage_path):
            try:
                with open(self.storage_path, "r", encoding="utf-8") as f:
                    all_data = json.load(f)
                self._data = all_data.get(self.name, [])
            except Exception:
                self._data = []

    def _save(self):
        try:
            all_data = {}
            if os.path.exists(self.storage_path):
                with open(self.storage_path, "r", encoding="utf-8") as f:
                    all_data = json.load(f)
            all_data[self.name] = self._data
            with open(self.storage_path, "w", encoding="utf-8") as f:
                json.dump(all_data, f)
        except Exception as e:
            log.warning("Local DB save warning: %s", e)

    @staticmethod
    def _matches(doc, query):
        if not query:
            return True
        for k, v in query.items():
            if isinstance(v, dict) and "$in" in v:
                if doc.get(k) not in v["$in"]:
                    return False
            elif doc.get(k) != v:
                return False
        return True

    def create_index(self, *args, **kwargs):
        pass

    def find_one(self, query):
        with self._lock:
            self._load()
            for doc in self._data:
                if self._matches(doc, query):
                    return dict(doc)
            return None

    def find(self, query=None):
        with self._lock:
            self._load()
            matched = [dict(d) for d in self._data if self._matches(d, query or {})]
            return _LocalCursor(matched)

    def count_documents(self, query=None):
        with self._lock:
            self._load()
            return sum(1 for d in self._data if self._matches(d, query or {}))

    def insert_one(self, doc):
        with self._lock:
            self._load()
            self._data.append(dict(doc))
            self._save()

    def update_one(self, query, update, upsert=False):
        with self._lock:
            self._load()
            for doc in self._data:
                if self._matches(doc, query):
                    if "$set" in update:
                        doc.update(update["$set"])
                    if "$inc" in update:
                        for k, v in update["$inc"].items():
                            doc[k] = doc.get(k, 0) + v
                    self._save()
                    return
            if upsert:
                new_doc = dict(query)
                if "$setOnInsert" in update:
                    new_doc.update(update["$setOnInsert"])
                if "$set" in update:
                    new_doc.update(update["$set"])
                if "$inc" in update:
                    for k, v in update["$inc"].items():
                        new_doc[k] = new_doc.get(k, 0) + v
                self._data.append(new_doc)
                self._save()

    def find_one_and_update(self, query, update, upsert=False, return_document=None):
        with self._lock:
            self._load()
            for doc in self._data:
                if self._matches(doc, query):
                    if "$set" in update:
                        doc.update(update["$set"])
                    if "$inc" in update:
                        for k, v in update["$inc"].items():
                            doc[k] = doc.get(k, 0) + v
                    self._save()
                    return dict(doc)
            if upsert:
                new_doc = dict(query)
                if "$set" in update:
                    new_doc.update(update["$set"])
                if "$inc" in update:
                    for k, v in update["$inc"].items():
                        new_doc[k] = new_doc.get(k, 0) + v
                self._data.append(new_doc)
                self._save()
                return dict(new_doc)
            return None

    def delete_one(self, query):
        with self._lock:
            self._load()
            for i, doc in enumerate(self._data):
                if self._matches(doc, query):
                    self._data.pop(i)
                    self._save()
                    return

    def aggregate(self, pipeline):
        with self._lock:
            self._load()
            docs = [dict(d) for d in self._data]
            for stage in pipeline:
                if "$match" in stage:
                    docs = [d for d in docs if self._matches(d, stage["$match"])]
                elif "$group" in stage:
                    grp = stage["$group"]
                    id_expr = grp["_id"]
                    groups = {}
                    for d in docs:
                        if isinstance(id_expr, str) and id_expr.startswith("$"):
                            key = d.get(id_expr[1:])
                        elif isinstance(id_expr, dict):
                            key = tuple((k, d.get(v[1:])) for k, v in id_expr.items())
                        else:
                            key = id_expr
                        if key not in groups:
                            row = {"_id": dict(key) if isinstance(id_expr, dict) else key}
                            for gk, gv in grp.items():
                                if gk == "_id":
                                    continue
                                if "$min" in gv:
                                    row[gk] = d.get(gv["$min"][1:], 0)
                                elif "$sum" in gv:
                                    row[gk] = 0
                            groups[key] = row
                        row = groups[key]
                        for gk, gv in grp.items():
                            if gk == "_id":
                                continue
                            if "$min" in gv:
                                val = d.get(gv["$min"][1:], 0)
                                if val < row[gk]:
                                    row[gk] = val
                            elif "$sum" in gv:
                                row[gk] += gv["$sum"]
                    docs = list(groups.values())
                elif "$sort" in stage:
                    for k, d in reversed(list(stage["$sort"].items())):
                        rev = (d == DESCENDING or d == -1)
                        if "." in k:
                            p1, p2 = k.split(".", 1)
                            docs.sort(key=lambda x: (x.get(p1) or {}).get(p2, 0), reverse=rev)
                        else:
                            docs.sort(key=lambda x: x.get(k, 0), reverse=rev)
            return docs


if config.MONGODB_URI:
    _client = MongoClient(config.MONGODB_URI)
    _db = _client.get_default_database(default="cinehub")
    videos_col = _db["videos"]
    unlocks_col = _db["unlocks"]
    history_col = _db["history"]
    users_col = _db["users"]
    ad_events_col = _db["ad_events"]
    counters_col = _db["counters"]
    settings_col = _db["settings"]
    pending_col = _db["pending_rewards"]
else:
    _client = None
    _db = None
    _local_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "local_db.json")
    videos_col = _LocalCollection("videos", _local_path)
    unlocks_col = _LocalCollection("unlocks", _local_path)
    history_col = _LocalCollection("history", _local_path)
    users_col = _LocalCollection("users", _local_path)
    ad_events_col = _LocalCollection("ad_events", _local_path)
    counters_col = _LocalCollection("counters", _local_path)
    settings_col = _LocalCollection("settings", _local_path)
    pending_col = _LocalCollection("pending_rewards", _local_path)


def init_db():
    if _client is not None:
        _client.admin.command("ping")
        videos_col.create_index("category")
        history_col.create_index([("user_id", 1), ("watched_at", DESCENDING)])
        ad_events_col.create_index([("video_id", 1), ("ad_number", 1), ("event", 1)])
        pending_col.create_index("created_at", expireAfterSeconds=3600)  # auto-expire unused tokens after 1h
        log.info("Connected to MongoDB: %s", _db.name)
    else:
        log.warning("MONGODB_URI is not set — using local_db.json fallback until MONGODB_URI is configured.")


def _next_id(name: str) -> int:
    doc = counters_col.find_one_and_update(
        {"_id": name}, {"$inc": {"seq": 1}}, upsert=True, return_document=ReturnDocument.AFTER
    )
    return doc["seq"]


# ---------- settings (admin-editable, no redeploy needed) ----------
def get_setting(key, default=None):
    doc = settings_col.find_one({"_id": key})
    return doc["value"] if doc else default


def set_setting(key, value):
    settings_col.update_one({"_id": key}, {"$set": {"value": value}}, upsert=True)


# ---------- users ----------
def upsert_user(user_id, username):
    users_col.update_one(
        {"_id": user_id},
        {"$set": {"username": username}, "$setOnInsert": {"first_seen": int(time.time())}},
        upsert=True,
    )


# ---------- videos ----------
def reserve_video_id():
    """Allocate a video ID before the storage copy is sent, so the ID can be
    included in that message's caption for later identification."""
    return _next_id("video_id")


def insert_video(video_id, title, category, description, thumbnail_file_id, storage_chat_id,
                  storage_message_id, added_by):
    videos_col.insert_one({
        "_id": video_id, "id": video_id, "title": title, "category": category,
        "description": description or "",
        "thumbnail_file_id": thumbnail_file_id, "storage_chat_id": storage_chat_id,
        "storage_message_id": storage_message_id,
        "added_by": added_by, "added_at": int(time.time()),
    })
    return video_id


def add_video(title, category, description, thumbnail_file_id, storage_chat_id, storage_message_id, added_by):
    video_id = reserve_video_id()
    return insert_video(video_id, title, category, description, thumbnail_file_id, storage_chat_id,
                         storage_message_id, added_by)


def get_videos(category, offset=0, limit=8):
    query = {} if not category or category.lower() == "all" else {"category": category}
    cursor = videos_col.find(query).sort("_id", DESCENDING).skip(offset).limit(limit)
    return list(cursor)


def count_videos(category):
    query = {} if not category or category.lower() == "all" else {"category": category}
    return videos_col.count_documents(query)


def get_categories():
    """Distinct category names in the order they first appeared, for dynamic tabs."""
    pipeline = [
        {"$group": {"_id": "$category", "first_at": {"$min": "$added_at"}}},
        {"$sort": {"first_at": 1}},
    ]
    return [doc["_id"] for doc in videos_col.aggregate(pipeline)]


def get_video(video_id):
    return videos_col.find_one({"_id": video_id})


def delete_video(video_id):
    videos_col.delete_one({"_id": video_id})


def update_video(video_id, **fields):
    """Partial update — only keys with a non-None value are changed."""
    fields = {k: v for k, v in fields.items() if v is not None}
    if fields:
        videos_col.update_one({"_id": video_id}, {"$set": fields})


def list_all_videos():
    return list(videos_col.find({}).sort("_id", DESCENDING))


# ---------- unlock / ad-task flow ----------
def _unlock_key(user_id, video_id):
    return f"{user_id}:{video_id}"


def get_unlock(user_id, video_id):
    return unlocks_col.find_one({"_id": _unlock_key(user_id, video_id)})


def is_still_unlocked(unlock_row, ttl_seconds):
    """True only if unlocked AND within the TTL window since unlock — after
    that the user has to complete both ad tasks again."""
    if not unlock_row or not unlock_row.get("unlocked") or not unlock_row.get("unlocked_at"):
        return False
    return (int(time.time()) - unlock_row["unlocked_at"]) < ttl_seconds


def bulk_get_unlocks(user_id, video_ids):
    """{video_id: unlock_row} for every id in video_ids — one query instead of
    N, used to paint the locked/countdown overlay on grid cards."""
    keys = [_unlock_key(user_id, vid) for vid in video_ids]
    rows = unlocks_col.find({"_id": {"$in": keys}})
    return {r["video_id"]: r for r in rows}


def locked_remaining_seconds(unlock_row, ttl_seconds):
    """Seconds left before this specific video re-unlocks (0 if not locked)."""
    if not is_still_unlocked(unlock_row, ttl_seconds):
        return 0
    return max(0, ttl_seconds - (int(time.time()) - unlock_row["unlocked_at"]))


def start_unlock(user_id, video_id):
    now = int(time.time())
    unlocks_col.update_one(
        {"_id": _unlock_key(user_id, video_id)},
        {"$set": {
            "user_id": user_id, "video_id": video_id, "ad1_shown_at": now,
            "ad1_clicked_at": None, "ad2_shown_at": None, "ad2_clicked_at": None,
            "unlocked": False,
        }},
        upsert=True,
    )
    return now


def mark_ad1_clicked(user_id, video_id):
    now = int(time.time())
    unlocks_col.update_one(
        {"_id": _unlock_key(user_id, video_id)},
        {"$set": {"ad1_clicked_at": now, "ad2_shown_at": now}},
    )
    return now


def mark_unlocked(user_id, video_id):
    now = int(time.time())
    unlocks_col.update_one(
        {"_id": _unlock_key(user_id, video_id)},
        {"$set": {"ad2_clicked_at": now, "unlocked": True, "unlocked_at": now}},
    )


def create_pending_reward(user_id, video_id, step):
    """Issue a one-time unguessable token (the Monetag `ymid`) before showing
    a rewarded ad, so the postback that comes back later can be matched to
    this exact attempt without trusting anything the client says."""
    token = secrets.token_urlsafe(20)
    pending_col.insert_one({
        "_id": token, "user_id": user_id, "video_id": video_id, "step": step,
        "created_at": int(time.time()), "used": False,
    })
    return token


def consume_pending_reward(token):
    """Atomically marks a pending token used and returns it — returns None if
    the token doesn't exist or was already consumed (prevents replay)."""
    return pending_col.find_one_and_update(
        {"_id": token, "used": False},
        {"$set": {"used": True, "used_at": int(time.time())}},
    )


def mark_ad_verified(user_id, video_id, step):
    """Set once Monetag's server-to-server postback confirms a *paid* reward
    event for this attempt — independent of anything the client reports."""
    unlocks_col.update_one(
        {"_id": _unlock_key(user_id, video_id)},
        {"$set": {f"ad{step}_verified": True}},
    )


def log_ad_event(user_id, video_id, ad_number, event, link):
    ad_events_col.insert_one({
        "user_id": user_id, "video_id": video_id, "ad_number": ad_number,
        "event": event, "link": link, "ts": int(time.time()),
    })


def ad_stats(video_id=None):
    """Returns per (video_id, ad_number, event) impression/click counts."""
    pipeline = []
    if video_id is not None:
        pipeline.append({"$match": {"video_id": video_id}})
    pipeline += [
        {"$group": {
            "_id": {"video_id": "$video_id", "ad_number": "$ad_number", "event": "$event"},
            "c": {"$sum": 1},
        }},
        {"$sort": {"_id.video_id": 1, "_id.ad_number": 1}},
    ]
    return [
        {"video_id": d["_id"]["video_id"], "ad_number": d["_id"]["ad_number"],
         "event": d["_id"]["event"], "c": d["c"]}
        for d in ad_events_col.aggregate(pipeline)
    ]


def add_history(user_id, video_id):
    history_col.insert_one({"user_id": user_id, "video_id": video_id, "watched_at": int(time.time())})


def get_user_history(user_id, limit=15):
    rows = list(
        history_col.find({"user_id": user_id}).sort("watched_at", DESCENDING).limit(limit)
    )
    video_ids = {r["video_id"] for r in rows}
    videos_by_id = {v["_id"]: v for v in videos_col.find({"_id": {"$in": list(video_ids)}})}
    out = []
    for r in rows:
        v = videos_by_id.get(r["video_id"])
        if not v:
            continue
        out.append({"title": v["title"], "category": v["category"], "watched_at": r["watched_at"]})
    return out
