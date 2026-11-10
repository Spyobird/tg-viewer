"""Parse Telegram Desktop JSON exports and compute chat statistics."""

import math
import re
from bisect import bisect_right
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from statistics import median

from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

# shortcut: English-only stopword list, extend for other languages if top words look noisy.
STOPWORDS = set(
    "the and you that for are with this have not but was what all can just your "
    "they its it's don't i'm get got like yeah yes too out now how will one would "
    "there their about from then them when were been has had did does who why him "
    "her his she our more some see also any okay ok lol haha".split()
)
WORD_RE = re.compile(r"[^\W\d_]{3,}")
# shortcut: single code points + flag pairs; ZWJ sequences split into parts and skin tones/VS16 are dropped,
# switch to the `emoji` package if exact grapheme counts matter.
EMOJI_RE = re.compile(
    "[\U0001F1E6-\U0001F1FF]{2}|[\U0001F300-\U0001F3FA\U0001F400-\U0001FAFF\u2600-\u27BF\u2300-\u23FF\u2B50\u2B55]"
)
CONVO_GAP = timedelta(hours=6)
RESPONSE_BUCKETS = (["<1m", "1-5m", "5-15m", "15-60m", "1-6h"], [60, 300, 900, 3600])
SENTIMENT = SentimentIntensityAnalyzer()


def plain_text(text) -> str:
    """Telegram stores text as a string or a list of strings / {"type", "text"} entities."""
    if isinstance(text, str):
        return text
    return "".join(p if isinstance(p, str) else p.get("text", "") for p in text or [])


def _message(m: dict) -> dict:
    kind = m.get("media_type") or ("photo" if "photo" in m else "file" if "file" in m else None)
    return {
        "id": m.get("id"),
        "date": m.get("date", ""),
        "from": m.get("from") or m.get("actor") or "Unknown",
        "service": m.get("type") == "service",
        "action": m.get("action"),
        "text": plain_text(m.get("text", "")),
        "media": kind,
        "sticker": m.get("sticker_emoji"),
        "reply_to": m.get("reply_to_message_id"),
        "forwarded_from": m.get("forwarded_from"),
        # custom/paid reactions have no "emoji", so show their type instead.
        "reactions": [{"emoji": r.get("emoji") or r.get("type", "?"), "count": r.get("count", 0),
                       "from": [x.get("from") for x in r.get("recent", []) if x.get("from")]}
                      for r in m.get("reactions", [])],
    }


def load_export(data: dict) -> list[dict]:
    """Accept a single-chat export or a full-account export; return a list of chats."""
    if "messages" in data:
        raw = [data]
    elif "chats" in data:
        raw = data["chats"].get("list", []) + data.get("left_chats", {}).get("list", [])
    else:
        raise ValueError("Not a Telegram export: expected 'messages' or 'chats' key")
    return [
        {
            "id": i,
            "name": c.get("name") or ("Saved Messages" if c.get("type") == "saved_messages" else "Unnamed"),
            "type": c.get("type", ""),
            "messages": [_message(m) for m in c.get("messages", [])],
        }
        for i, c in enumerate(raw)
    ]


def _mattr(toks: list[str], w: int = 50) -> float:
    """Moving-average type-token ratio with a sliding Counter (O(n)); plain TTR below one window."""
    if len(toks) < w:
        return round(len(set(toks)) / len(toks), 3) if toks else 0.0
    win = Counter(toks[:w])
    total = len(win)
    for old, new in zip(toks, toks[w:]):
        win[old] -= 1
        if not win[old]:
            del win[old]
        win[new] += 1
        total += len(win)
    return round(total / (len(toks) - w + 1) / w, 3)


def _pctl(xs: list[float], q: float) -> int | None:
    """Nearest-rank percentile of an already sorted list."""
    return round(xs[math.ceil(q * len(xs)) - 1]) if xs else None


def _avg(xs) -> float | None:
    return round(sum(xs) / len(xs), 3) if xs else None


def compute_stats(messages: list[dict]) -> dict:
    msgs = [m for m in messages if not m["service"] and m["date"]]
    heatmap = [[0] * 24 for _ in range(7)]  # [weekday Mon=0][hour], exporter's local time
    months, days, words, emoji, weeks = Counter(), Counter(), Counter(), Counter(), Counter()
    reactions, given = Counter(), Counter()
    week_sent = defaultdict(list)  # (week Monday, sender or None) -> compound scores
    week_resp = defaultdict(list)  # (week Monday, sender) -> reply times in seconds
    senders: dict[str, dict] = {}
    acc: dict[str, dict] = {}  # per-sender working data, folded into senders at the end
    convos = []  # [start, end] datetimes
    prev = None  # (datetime, sender) of previous message
    run = 0
    for m in msgs:
        dt = datetime.fromisoformat(m["date"])
        who, text, month = m["from"], m["text"], m["date"][:7]
        heatmap[dt.weekday()][dt.hour] += 1
        months[month] += 1
        days[m["date"][:10]] += 1
        week = dt.date() - timedelta(dt.weekday())
        weeks[(week, who)] += 1
        toks = WORD_RE.findall(text.lower())
        words.update(t for t in toks if t not in STOPWORDS)
        s = senders.setdefault(who, {"name": who, "messages": 0, "words": 0, "media": 0, "initiations": 0, "ends": 0,
                                     "double_texts": 0, "questions": 0, "longest_monologue": 0,
                                     "reactions_received": 0, "left_on_read": 0, "got_left_on_read": 0})
        a = acc.setdefault(who, {"resp": [], "buckets": [0] * 5,
                                 "toks": [], "emoji": Counter(), "sent": [], "night": 0})
        s["messages"] += 1
        s["words"] += len(text.split())
        s["media"] += bool(m["media"] or m["sticker"])
        s["questions"] += "?" in text
        a["night"] += dt.hour < 6
        for r in m.get("reactions", []):
            reactions[r["emoji"]] += r["count"]
            s["reactions_received"] += r["count"]
            # shortcut: Telegram only lists "recent" reactors, so given counts undercount on popular messages.
            given.update(r["from"])
        a["toks"] += toks
        em = EMOJI_RE.findall(text) + EMOJI_RE.findall(m["sticker"] or "")
        emoji.update(em)
        a["emoji"].update(em)
        if text.strip():
            c = SENTIMENT.polarity_scores(text)["compound"]
            a["sent"].append(c)
            week_sent[(week, None)].append(c)
            week_sent[(week, who)].append(c)

        same = prev is not None and prev[1] == who
        if prev is None or dt - prev[0] > CONVO_GAP:
            if prev:
                senders[prev[1]]["ends"] += 1
                if not same:  # answered someone else only after 6h+ of silence
                    s["left_on_read"] += 1
                    senders[prev[1]]["got_left_on_read"] += 1
            s["initiations"] += 1
            convos.append([dt, dt])
        elif not same:
            secs = (dt - prev[0]).total_seconds()
            a["resp"].append(secs)
            week_resp[(week, who)].append(secs)
            a["buckets"][bisect_right(RESPONSE_BUCKETS[1], secs)] += 1
        if same and dt - prev[0] > timedelta(hours=1):
            s["double_texts"] += 1
        run = run + 1 if same else 1
        s["longest_monologue"] = max(s["longest_monologue"], run)
        convos[-1][1] = dt
        prev = (dt, who)
    if prev:
        senders[prev[1]]["ends"] += 1

    for name, s in senders.items():
        a = acc[name]
        resp = sorted(a["resp"])
        s.update(
            initiation_rate=round(s["initiations"] / len(convos), 3),
            median_response_s=round(median(a["resp"])) if a["resp"] else None,
            p90_response_s=_pctl(resp, 0.9),
            p95_response_s=_pctl(resp, 0.95),
            p99_response_s=_pctl(resp, 0.99),
            words_per_msg=round(s["words"] / s["messages"], 2),
            vocab=len(set(a["toks"])),
            lexical_diversity=_mattr(a["toks"]),
            emoji=a["emoji"].total(),
            top_emoji=[{"emoji": e, "count": c} for e, c in a["emoji"].most_common(5)],
            sentiment=_avg(a["sent"]),
            night_share=round(a["night"] / s["messages"], 3),
            reactions_given=given[name],
        )

    # Fill empty months so the timeline shows gaps instead of hiding them.
    timeline = []
    if months:
        y, mo = map(int, min(months).split("-"))
        end = max(months)
        while (key := f"{y:04d}-{mo:02d}") <= end:
            timeline.append({"month": key, "count": months[key]})
            y, mo = (y + 1, 1) if mo == 12 else (y, mo + 1)

    weekly = []
    if weeks:
        wk, last = min(w for w, _ in weeks), max(w for w, _ in weeks)
        while wk <= last:
            by = {n: weeks[(wk, n)] for n in senders if weeks[(wk, n)]}
            weekly.append({"week": wk.isoformat(), "total": sum(by.values()), "by_sender": by})
            wk += timedelta(7)

    active = [date.fromisoformat(d) for d in sorted(days)]
    streak = silence = None
    i = 0
    for j in range(1, len(active) + 1):
        if j == len(active) or (active[j] - active[j - 1]).days > 1:
            if not streak or j - i > streak["days"]:
                streak = {"days": j - i, "start": active[i].isoformat(), "end": active[j - 1].isoformat()}
            i = j
    for d1, d2 in zip(active, active[1:]):
        if not silence or (d2 - d1).days > silence["days"]:
            silence = {"days": (d2 - d1).days, "from": d1.isoformat(), "to": d2.isoformat()}

    return {
        "total": len(msgs),
        "first": msgs[0]["date"] if msgs else None,
        "last": msgs[-1]["date"] if msgs else None,
        "active_days": len(days),
        "busiest_days": [{"day": d, "count": c} for d, c in days.most_common(5)],
        "heatmap": heatmap,
        "timeline": timeline,
        "senders": sorted(senders.values(), key=lambda s: -s["messages"]),
        "top_words": [{"word": w, "count": c} for w, c in words.most_common(100)],
        "calendar": [{"day": d, "count": days[d]} for d in sorted(days)],
        "weekly": weekly,
        "sentiment_timeline": [
            {"week": w["week"], "score": _avg(week_sent.get((wk := date.fromisoformat(w["week"]), None))),
             "by_sender": {n: _avg(week_sent[(wk, n)]) for n in senders if (wk, n) in week_sent}}
            for w in weekly
        ],
        "emoji": [{"emoji": e, "count": c} for e, c in emoji.most_common(30)],
        "reactions": [{"emoji": e, "count": c} for e, c in reactions.most_common(20)],
        "conversations": {
            "count": len(convos),
            "gap_hours": CONVO_GAP.seconds // 3600,
            "avg_messages": round(len(msgs) / len(convos), 1) if convos else 0.0,
            "avg_minutes": round(sum((e - b).total_seconds() for b, e in convos) / 60 / len(convos), 1) if convos else 0.0,
        },
        "streak": streak,
        "longest_silence": silence,
        "reply_timeline": [
            {"week": w["week"], "by_sender": {n: round(median(week_resp[(wk, n)]))
                                              for n in senders if (wk := date.fromisoformat(w["week"]), n) in week_resp}}
            for w in weekly
        ],
        "response_buckets": {"labels": RESPONSE_BUCKETS[0], "by_sender": {n: a["buckets"] for n, a in acc.items()}},
    }
