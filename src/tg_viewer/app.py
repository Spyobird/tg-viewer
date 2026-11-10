import json
from bisect import bisect_left
from collections import Counter

from flask import Flask, abort, jsonify, request

from .analysis import compute_stats, load_export

app = Flask(__name__, static_folder="static", static_url_path="")

# shortcut: one in-memory import for a single local user; re-importing replaces it, restart clears it.
CHATS: list[dict] = []


def _chat(cid: int) -> dict:
    if not 0 <= cid < len(CHATS):
        abort(404)
    return CHATS[cid]


@app.get("/")
def index():
    return app.send_static_file("index.html")


@app.post("/api/import")
def import_export():
    try:
        chats = load_export(json.loads(request.get_data()))
    except (ValueError, AttributeError, TypeError) as e:
        return jsonify(error=f"Could not read export: {e}"), 400
    CHATS[:] = chats
    return jsonify([{"id": c["id"], "name": c["name"], "type": c["type"], "count": len(c["messages"]),
                     "senders": _top_senders(c["messages"])} for c in chats])


def _top_senders(msgs: list[dict]) -> list[str]:
    """Same ranking as compute_stats' senders, so per-person colours match the Analytics tab."""
    return [n for n, _ in Counter(m["from"] for m in msgs if not m["service"] and m["date"]).most_common(8)]


@app.get("/api/chats/<int:cid>/messages")
def messages(cid: int):
    """Newest-first paging: offset counts back from the end of the (filtered) chat.
    With ?date=YYYY-MM-DD the page instead starts at that day's first message (or is the newest page)."""
    msgs = _chat(cid)["messages"]
    if q := request.args.get("q", "").strip().lower():
        msgs = [m for m in msgs if q in m["text"].lower() or q in m["from"].lower()]
    offset = request.args.get("offset", 0, type=int)
    limit = min(request.args.get("limit", 200, type=int), 1000)
    if day := request.args.get("date"):
        offset = max(len(msgs) - bisect_left(msgs, day, key=lambda m: m["date"][:10]) - limit, 0)
    end = max(len(msgs) - offset, 0)
    return jsonify(total=len(msgs), offset=offset, messages=msgs[max(end - limit, 0) : end])


@app.get("/api/chats/<int:cid>/stats")
def stats(cid: int):
    chat = _chat(cid)
    if "stats" not in chat:
        chat["stats"] = compute_stats(chat["messages"])
    return jsonify(chat["stats"])
