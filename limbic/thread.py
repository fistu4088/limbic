"""Conversation thread + excitement.

Tracks the current conversation: who with, what about, and how engaging it
is for this persona. Liked topics, novelty, humour (and, when consented,
romance/desire) raise engagement; engagement produces excitement, and a
highly engaging thread that suddenly goes quiet is what triggers a check-in.
"""
from __future__ import annotations

import math

from .needs import adult_ok, romance_ok

LN2 = math.log(2)


def new_thread(partner: str | None, now: float, topic: str | None, private: bool) -> dict:
    return {"partner": partner, "started": now, "last_user": now, "last_agent": 0.0,
            "topic": topic, "topics": [topic] if topic else [], "engagement": 0.0,
            "peak": 0.0, "messages": 0, "checked_in": False, "private": private}


def on_message(engine, s, ap, now: float, speaker: str | None, private: bool, effects: dict) -> None:
    if not ap.interaction:
        return
    T = engine.t
    th = s.thread
    partner = speaker or ap.target
    if th is None or th["partner"] != partner or now - max(th["last_user"], th["last_agent"]) > T.thread_gap_min * 60:
        th = s.thread = new_thread(partner, now, ap.topic, private)
    if ap.topic and ap.topic != th["topic"]:
        th["topic"] = ap.topic
        if ap.topic not in th["topics"]:
            th["topics"] = (th["topics"] + [ap.topic])[-8:]
        th["engagement"] *= 0.6
    rel = s.relationships.get(partner or "")
    gain = (0.55 * max(0.0, ap.affinity) + 0.25 * ap.novelty * (1 if ap.affinity >= 0 else 0)
            + 0.25 * ap.humor + 0.15 * max(0.0, ap.valence))
    if romance_ok(T, rel):
        gain += 0.3 * ap.romance
    if adult_ok(T, rel):
        gain += 0.45 * ap.erotic
    gain -= 0.4 * max(0.0, -ap.affinity) + (0.3 if ap.rebuff else 0.0)
    gain = max(-1.0, min(1.0, gain))
    if gain >= 0:
        th["engagement"] += T.interest_gain * gain * (1 - th["engagement"])
    else:
        th["engagement"] = max(0.0, th["engagement"] * (1 + 0.6 * gain))
    th["peak"] = max(th["peak"], th["engagement"])
    if now != th["last_user"] or th["messages"] == 0:
        th["messages"] += 1
    th["last_user"] = now
    th["checked_in"] = False
    th["private"] = th["private"] or private
    if gain > 0.15 and th["engagement"] > 0.3:
        amt = 0.35 * gain * th["engagement"]
        effects["emotions"]["excitement"] = round(engine._add_emotion(
            s, "excitement", None, amt, f"talking about {th['topic'] or 'something I enjoy'}"), 4)
    effects["engagement"] = round(th["engagement"], 4)


def on_agent_reply(s, now: float) -> None:
    s.reply_tracking = True
    if s.thread is not None:
        s.thread["last_agent"] = now


def step(engine, s, h: float, now: float) -> None:
    th = s.thread
    if th is None:
        return
    th["engagement"] *= math.exp(-h * LN2 / (engine.t.engagement_half_life_h * 3600.0))
