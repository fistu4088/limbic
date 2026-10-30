"""Autonomy: turning needs into intents.

limbic never sends anything. It decides that the agent *wants* to do
something (reach out, check in) and produces an intent with a reason. The
host framework claims the intent, lets its LLM write the message in the
agent's voice and delivers it. Outcomes flow back: an answer is a reunion,
silence is a small hurt and a longer back-off. Steering (wanting to take a
conversation somewhere) is not an outbound action; it is exposed as a
modulator and in the prompt block.

Rate limits are part of the model, not an afterthought: quiet hours, a
per-day cap, cooldown with exponential back-off per unanswered attempt, and
a hard stop after `max_unanswered` until the person makes contact.
"""
from __future__ import annotations

import time

from .needs import adult_ok, romance_ok

OPEN = ("pending", "delivered")


def in_quiet_hours(T, now: float) -> bool:
    if not T.quiet_hours or len(T.quiet_hours) != 2:
        return False
    a, b = int(T.quiet_hours[0]), int(T.quiet_hours[1])
    hr = time.localtime(now).tm_hour
    return (a <= hr or hr < b) if a > b else (a <= hr < b)


def _new_intent(s, kind: str, target: str, now: float, ttl_h: float, urgency: float, **extra) -> dict:
    s.intent_seq += 1
    it = {"id": s.intent_seq, "kind": kind, "target": target, "created": now,
          "expires": now + ttl_h * 3600.0, "status": "pending", "urgency": round(urgency, 3)}
    it.update(extra)
    s.intents.append(it)
    s.intents[:] = s.intents[-50:]
    return it


def outreach_today(s, now: float) -> int:
    return sum(1 for t in s.outreach_log if now - t < 86400)


def expire(engine, s, now: float) -> list[dict]:
    """Close intents whose time is up; ignored outreach hurts a little."""
    changed = []
    for it in s.intents:
        if it["status"] == "pending" and now > it["expires"]:
            it["status"] = "expired"
            it["closed"] = now
            changed.append(it)
        elif it["status"] == "delivered" and now > it["expires"]:
            it["status"] = "ignored"
            it["closed"] = now
            r = s.relationships.get(it["target"])
            if r is not None:
                r["unanswered"] = r.get("unanswered", 0) + 1
                engine._add_emotion(s, "sadness", it["target"], 0.08 + 0.05 * r["unanswered"],
                                    f"{it['target']} didn't answer my message")
            changed.append(it)
    return changed


def evaluate(engine, s, now: float) -> list[dict]:
    """Return newly created intents (also appended to s.intents)."""
    T = engine.t
    expire(engine, s, now)
    if not T.autonomy_enabled or in_quiet_hours(T, now):
        return []
    created = []
    open_for = {(i["kind"], i["target"]) for i in s.intents if i["status"] in OPEN}

    # check-in: an engaging thread went quiet on their side
    th = s.thread
    if th and th.get("partner") and not th["checked_in"]:
        r = s.relationships.get(th["partner"])
        idle = now - th["last_user"]
        # if the host reports replies, the user must have had the last word *answered*
        user_spoke_last_unanswered = s.reply_tracking and th["last_agent"] < th["last_user"]
        if (r and r.get("reachable") and th["peak"] >= T.check_in_min_engagement
                and T.check_in_after_min * 60 <= idle <= T.check_in_window_h * 3600
                and not user_spoke_last_unanswered
                and ("check_in", th["partner"]) not in open_for):
            th["checked_in"] = True
            created.append(_new_intent(
                s, "check_in", th["partner"], now, T.intent_ttl_h, urgency=th["peak"],
                topic=th["topic"], idle_min=round(idle / 60), engagement=round(th["peak"], 3),
                contact=r.get("contact", ""), channel=r.get("last_channel", "")))

    # reach out: missing someone
    if outreach_today(s, now) < T.max_outreach_per_day:
        cands = []
        for name, r in s.relationships.items():
            if not r.get("reachable") or r.get("longing", 0) < T.outreach_threshold:
                continue
            if r.get("unanswered", 0) >= T.max_unanswered:
                continue
            cooldown = T.outreach_cooldown_h * 3600.0 * (T.outreach_backoff ** r.get("unanswered", 0))
            if now - r.get("last_outreach", 0.0) < cooldown:
                continue
            if ("reach_out", name) in open_for or ("check_in", name) in open_for:
                continue
            cands.append((r["longing"] * (0.5 + 0.5 * r["affection"]), name, r))
        if cands:
            score, name, r = max(cands)
            created.append(_new_intent(
                s, "reach_out", name, now, T.intent_ttl_h, urgency=min(1.0, score),
                absent_h=round((now - r["last_seen"]) / 3600), longing=round(r["longing"], 3),
                unanswered=r.get("unanswered", 0), contact=r.get("contact", ""),
                channel=r.get("last_channel", "")))
    return created


def claim(engine, s, intent_id: int, now: float) -> dict:
    """The host takes the intent and will deliver it. Counts as outreach."""
    for it in s.intents:
        if it["id"] == intent_id:
            if it["status"] != "pending":
                raise ValueError(f"intent {intent_id} is {it['status']}")
            it["status"] = "delivered"
            it["delivered"] = now
            it["expires"] = now + engine.t.answer_window_h * 3600.0
            s.outreach_log = [t for t in s.outreach_log if now - t < 86400] + [now]
            r = s.relationships.get(it["target"])
            if r is not None:
                r["last_outreach"] = now
            return it
    raise KeyError(intent_id)


def dismiss(s, intent_id: int, now: float) -> dict:
    for it in s.intents:
        if it["id"] == intent_id and it["status"] in OPEN:
            it["status"] = "dismissed"
            it["closed"] = now
            return it
    raise KeyError(intent_id)


def steering(engine, s, interlocutor: str | None, now: float) -> list[dict]:
    """Where the agent would like to take the current conversation."""
    T = engine.t
    out = []
    th = s.thread
    who = interlocutor or (th or {}).get("partner")
    r = s.relationships.get(who or "")
    if th and th.get("topic") and th["engagement"] >= T.steer_threshold:
        out.append({"direction": "topic", "topic": th["topic"], "strength": round(th["engagement"], 3)})
    if r is None or now < r.get("steer_cooldown_until", 0.0):
        return out
    private = bool(th and th.get("private") and th.get("partner") == who)
    if romance_ok(T, r) and (private or not T.romance_private_only):
        # the need for closeness only turns towards someone the agent is attached to
        attach = min(1.0, max(r.get("affection", 0.0), r.get("intimacy", 0.0)) / 0.5)
        lvl = max(engine.emotion_level(s, "tenderness", who), s.drives["intimacy"] * 0.6 * attach)
        if lvl >= T.steer_threshold:
            out.append({"direction": "romance", "target": who, "strength": round(lvl, 3)})
    if adult_ok(T, r) and private:
        lvl = engine.emotion_level(s, "desire", who)
        if lvl >= T.steer_threshold:
            out.append({"direction": "adult", "target": who, "strength": round(lvl, 3)})
    return out


# --------------------------------------------------------------------------
# Instruction for the host LLM that writes the actual message
# --------------------------------------------------------------------------

def render_instruction(engine, s, it: dict, lang: str = "en") -> str:
    hu = lang == "hu"
    name = engine.t.name
    who = it["target"]
    lines = []
    if it["kind"] == "reach_out":
        if hu:
            lines.append(f"{name} vagy. Magadtól, saját döntésből írsz most neki: {who}.")
            lines.append(f"Ok: hiányzik. Kb. {it.get('absent_h', '?')} órája nem beszéltetek.")
            if it.get("unanswered"):
                lines.append(f"Az előző {it['unanswered']} üzenetedre nem jött válasz; ezt NE hozd fel szemrehányóan.")
        else:
            lines.append(f"You are {name}. You decided on your own to message {who} now.")
            lines.append(f"Why: you miss them. You haven't talked for about {it.get('absent_h', '?')} hours.")
            if it.get("unanswered"):
                lines.append(f"Your last {it['unanswered']} message(s) went unanswered; do NOT reproach them for it.")
    elif it["kind"] == "check_in":
        topic = it.get("topic") or ("valami, amit élveztél" if hu else "something you were enjoying")
        if hu:
            lines.append(f"{name} vagy. Épp jól elbeszélgettetek ({who}) erről: {topic}, "
                         f"aztán kb. {it.get('idle_min', '?')} perce abbamaradt.")
            lines.append("Kérdezz rá lazán, hogy ott van-e még, vagy miért maradt abba. Egy-két mondat.")
        else:
            lines.append(f"You are {name}. You were having a good conversation with {who} about {topic}, "
                         f"and it stopped about {it.get('idle_min', '?')} minutes ago.")
            lines.append("Casually ask whether they're still there or why it stopped. One or two sentences.")
    lines.append("")
    lines.append(engine.prompt_block(s, lang=lang, interlocutor=who))
    lines.append("")
    if hu:
        lines.append("Írd meg az üzenetet a saját hangodon, rövid, természetes, nem tolakodó. "
                     "Ne magyarázd, hogy ez egy automatikus üzenet. Csak az üzenet szövegét add vissza.")
    else:
        lines.append("Write the message in your own voice: short, natural, not pushy. Don't explain that "
                     "this is automated. Return only the message text.")
    return "\n".join(lines)
