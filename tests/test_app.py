import json

import pytest

from tg_viewer.analysis import compute_stats, load_export
from tg_viewer.app import app

CHAT = {
    "name": "Alice",
    "type": "personal_chat",
    "id": 1,
    "messages": [
        {"id": 1, "type": "service", "date": "2024-01-01T09:00:00", "actor": "Bob", "action": "phone_call", "text": ""},
        {"id": 2, "type": "message", "date": "2024-01-01T09:15:00", "from": "Bob", "text": "hello world"},
        {"id": 3, "type": "message", "date": "2024-01-01T09:30:00", "from": "Alice",
         "text": ["see ", {"type": "link", "text": "https://example.com"}, " world"]},
        {"id": 4, "type": "message", "date": "2024-03-05T23:59:00", "from": None, "photo": "photos/a.jpg", "text": ""},
    ],
}


def test_load_single_and_full_export():
    [chat] = load_export(CHAT)
    assert chat["name"] == "Alice" and len(chat["messages"]) == 4
    assert chat["messages"][2]["text"] == "see https://example.com world"
    assert chat["messages"][3]["from"] == "Unknown" and chat["messages"][3]["media"] == "photo"

    full = {"chats": {"list": [CHAT, {"type": "saved_messages", "messages": []}]}, "left_chats": {"list": []}}
    assert [c["name"] for c in load_export(full)] == ["Alice", "Saved Messages"]

    with pytest.raises(ValueError):
        load_export({"foo": 1})


def test_stats():
    s = compute_stats(load_export(CHAT)[0]["messages"])
    assert s["total"] == 3  # service message excluded
    assert s["heatmap"][0][9] == 2  # 2024-01-01 is a Monday
    assert s["heatmap"][1][23] == 1
    assert [m["month"] for m in s["timeline"]] == ["2024-01", "2024-02", "2024-03"]
    assert s["timeline"][1]["count"] == 0
    assert {k: s["senders"][0][k] for k in ("name", "messages", "words", "media")} == {
        "name": "Bob", "messages": 1, "words": 2, "media": 0}
    assert s["top_words"][0] == {"word": "world", "count": 2}
    assert s["active_days"] == 2


def _msg(i, date, who, text="", **kw):
    return {"id": i, "type": "message", "date": date, "from": who, "text": text, **kw}


def test_conversation_stats():
    chat = {"messages": [
        _msg(1, "2024-01-01T10:00:00", "A", "Hi there?"),
        _msg(2, "2024-01-01T10:02:00", "B", "I love this, it is wonderful! 😂😂", reactions=[
            {"type": "emoji", "count": 2, "emoji": "❤", "recent": [{"from": "A"}, {"from": "C"}]},
            {"type": "custom_emoji", "count": 1, "document_id": "5", "recent": [{"from": "A"}]}]),
        _msg(3, "2024-01-01T10:12:00", "A", "ok"),
        _msg(4, "2024-01-01T12:00:00", "A", "still there?", sticker_emoji="😂"),  # A double-texts after 1h48m
        _msg(5, "2024-01-02T09:00:00", "B", "morning"),  # >6h gap: B initiates, A ended convos 1 and 2
        _msg(6, "2024-01-02T09:30:00", "A", "hey"),
        _msg(7, "2024-01-20T09:00:00", "B", "back"),
    ]}
    s = compute_stats(load_export(chat)[0]["messages"])
    a, b = sorted(s["senders"], key=lambda x: x["name"])
    assert s["conversations"]["count"] == 3
    assert (a["initiations"], a["ends"], b["initiations"], b["ends"]) == (1, 2, 2, 1)
    assert a["median_response_s"] == 1200 and b["median_response_s"] == 120  # A: 600s & 1800s, B: 120s
    assert a["p90_response_s"] == 1800 and b["p90_response_s"] == 120
    assert (a["p95_response_s"], a["p99_response_s"], b["p99_response_s"]) == (1800, 1800, 120)
    assert (a["got_left_on_read"], b["left_on_read"], a["left_on_read"], b["got_left_on_read"]) == (2, 2, 0, 0)
    assert s["reply_timeline"][0]["by_sender"] == {"A": 1200, "B": 120} and s["reply_timeline"][1]["by_sender"] == {}
    assert (a["double_texts"], a["longest_monologue"], a["questions"]) == (1, 2, 2)
    assert s["response_buckets"]["by_sender"]["A"] == [0, 0, 1, 1, 0]
    assert s["streak"] == {"days": 2, "start": "2024-01-01", "end": "2024-01-02"}
    assert s["longest_silence"] == {"days": 18, "from": "2024-01-02", "to": "2024-01-20"}
    assert [(w["week"], w["total"]) for w in s["weekly"]] == [("2024-01-01", 6), ("2024-01-08", 0), ("2024-01-15", 1)]
    assert s["weekly"][1]["by_sender"] == {} and s["weekly"][0]["by_sender"] == {"A": 4, "B": 2}
    assert s["emoji"] == [{"emoji": "😂", "count": 3}] and b["top_emoji"] == [{"emoji": "😂", "count": 2}]
    assert b["sentiment"] > 0.2 and s["sentiment_timeline"][0]["by_sender"]["B"] > 0
    assert [d["day"] for d in s["calendar"]] == ["2024-01-01", "2024-01-02", "2024-01-20"]
    assert s["reactions"] == [{"emoji": "❤", "count": 2}, {"emoji": "custom_emoji", "count": 1}]
    assert (b["reactions_received"], a["reactions_given"], b["reactions_given"]) == (3, 2, 0)


def test_api_paging_and_search():
    c = app.test_client()
    assert c.post("/api/import", data=b"not json").status_code == 400
    r = c.post("/api/import", data=json.dumps(CHAT))
    assert r.json == [{"id": 0, "name": "Alice", "type": "personal_chat", "count": 4,
                       "senders": ["Bob", "Alice", "Unknown"]}]

    page = c.get("/api/chats/0/messages?limit=2").json
    assert page["total"] == 4 and [m["id"] for m in page["messages"]] == [3, 4]
    older = c.get("/api/chats/0/messages?limit=2&offset=2").json
    assert [m["id"] for m in older["messages"]] == [1, 2]
    assert c.get("/api/chats/0/messages?q=WORLD").json["total"] == 2
    jump = c.get("/api/chats/0/messages?date=2024-01-01&limit=1").json
    assert jump["offset"] == 3 and [m["id"] for m in jump["messages"]] == [1]
    late = c.get("/api/chats/0/messages?date=2030-01-01&limit=2").json  # past the end: newest page
    assert late["offset"] == 0 and [m["id"] for m in late["messages"]] == [3, 4]

    assert c.get("/api/chats/0/stats").json["total"] == 3
    assert c.get("/api/chats/9/stats").status_code == 404
    assert c.get("/").status_code == 200

