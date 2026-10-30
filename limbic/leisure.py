"""Leisure: free time driven by feelings, not by a schedule.

The agent has unlimited free time but only *does* something when the
emotion system says so: boredom / a craving for novelty is the motive, mood
is the gate (sad or depressed: doing nothing is a real choice), fatigue is
the brake (a rest period with hysteresis), and taste decides what: every
finished activity leaves an enjoyment score that makes the same thing more
(or less) tempting next time, worn down by having done it a lot lately.

limbic never runs anything. It decides, keeps the bookkeeping, and feels
what the host reports. A runner (see contrib/leisure) asks `decide`, does
the activity in small steps, reports each experience (which is appraised and
felt like any other event), asks `should_continue`, and finally `finish`es
with the agent's own rating. A conversation pauses the activity (bookmark);
"folytasd" resumes it; otherwise it stays as a bookmark the agent may come
back to the next time boredom returns.
"""
from __future__ import annotations

import math
import random
from typing import Any

LN2 = math.log(2)

# activity -> (fatigue per step, novelty satisfied per step, label hu, label en)
ACTIVITIES: dict[str, dict[str, Any]] = {
    # fatigue per step, novelty satisfied per step, max minutes per session
    "web":     {"fatigue": 0.006, "novelty": 0.10, "max_min": 35, "demand": 0.6, "hu": "netes olvasgatás", "en": "reading on the web"},
    "lector":  {"fatigue": 0.002, "novelty": 0.06, "max_min": 60, "demand": 0.35, "hu": "olvasás (manga/könyv)", "en": "reading a story"},
    "journal": {"fatigue": 0.006, "novelty": 0.03, "max_min": 20, "demand": 0.45, "hu": "naplóírás", "en": "writing in the journal"},
    "code":    {"fatigue": 0.030, "novelty": 0.12, "max_min": 40, "demand": 1.0, "hu": "kísérletezés scriptekkel", "en": "tinkering with scripts"},
}


def new_leisure() -> dict[str, Any]:
    return {"current": None, "taste": {}, "resting": False, "log": [], "seq": 0,
            "last_end": 0.0, "available": {}}


def ensure(s) -> dict[str, Any]:
    if not isinstance(getattr(s, "leisure", None), dict) or not s.leisure:
        s.leisure = new_leisure()
    lz = s.leisure
    for k, v in new_leisure().items():
        lz.setdefault(k, v)
    return lz


def label(act: str, lang: str = "hu") -> str:
    return ACTIVITIES.get(act, {}).get("hu" if lang == "hu" else "en", act)


def sessions_since(lz: dict, now: float, hours: float, act: str | None = None) -> int:
    return sum(1 for e in lz["log"] if now - e.get("end", 0) < hours * 3600 and (act is None or e["activity"] == act))


# --------------------------------------------------------------------------
# Inner signals
# --------------------------------------------------------------------------

def urge(engine, s, now: float | None = None) -> float:
    """How much the agent wants *something* to do (0..1): boredom proper, plus
    the restlessness of a few empty hours (nobody around, nothing going on)."""
    bored = engine.emotion_level(s, "boredom")
    nov = s.drives.get("novelty", 0.0)
    thr = engine.t.need_threshold
    u = 1.6 * bored + 0.8 * max(0.0, nov - 0.5 * thr) / (1 - 0.5 * thr)
    now = s.t if now is None else now
    lz = ensure(s)
    idle_h = (now - max(s.last_interaction or 0.0, lz.get("last_end", 0.0) or 0.0)) / 3600.0
    tau = max(engine.t.leisure_idle_tau_h, 1e-6)
    restless = 0.5 * (1 - math.exp(-max(0.0, idle_h) / tau)) * (0.5 + nov)
    return max(0.0, min(1.0, max(u, restless)))


def mood_gate(engine, s) -> float:
    """Willingness given the mood: 1 neutral, more when cheerful, little when sad."""
    p = s.mood["p"]
    if p < -0.45:      # depressed: almost nothing appeals
        g = 0.05
    elif p < -0.2:     # sad
        g = 0.3
    elif p < -0.07:    # glum
        g = 0.7
    else:
        g = 1.0 + 0.6 * min(p, 0.8)
    # low energy (not fatigue) dulls it a little
    g *= 1.0 + 0.3 * max(-1.0, min(0.0, s.mood["a"]))
    return max(0.0, g)


def sleepy(s) -> bool:
    return s.drives.get("sleepiness", 0.0) >= 0.6


def tiredness(s) -> float:
    """How tired she feels: exhaustion or sleepiness, whichever is worse."""
    return min(1.0, max(s.drives.get("fatigue", 0.0), 1.1 * s.drives.get("sleepiness", 0.0)))


def liking(engine, s, act: str, enjoy_now: float | None = None) -> float:
    """0..1 how much she likes this activity (taste, or how it's going right now)."""
    T = engine.t
    taste = ensure(s)["taste"].get(act, {})
    e = taste.get("enjoy", T.leisure_prior.get(act, 0.1) if isinstance(T.leisure_prior, dict) else 0.1)
    if enjoy_now is not None:
        e = 0.4 * e + 0.6 * enjoy_now
    return max(0.0, min(1.0, (e + 0.3) / 0.9))


def effort_cost(engine, s, act: str, enjoy_now: float | None = None) -> float:
    """What doing this costs her right now. No fixed "tired, must rest" line:
    tiredness makes demanding things expensive, but what she loves stays cheap,
    the way a tired person still reads one more chapter but won't start a project."""
    demand = ACTIVITIES.get(act, {}).get("demand", 0.6)
    return tiredness(s) * demand * (1 - 0.75 * liking(engine, s, act, enjoy_now))


def update_rest(engine, s) -> bool:
    """Feeling rested after being properly worn out is a small pleasure."""
    lz = ensure(s)
    f = s.drives.get("fatigue", 0.0)
    if f >= 0.65:
        lz["was_tired"] = True
    elif lz.get("was_tired") and f < 0.3:
        lz["was_tired"] = False
        engine._add_emotion(s, "joy", None, 0.12, "kipihentem magam")
    lz["resting"] = False      # kept for old clients; there is no forced rest any more
    return False


def appeal(engine, s, act: str, now: float) -> float:
    """How tempting this particular activity is right now."""
    T = engine.t
    lz = ensure(s)
    taste = lz["taste"].get(act, {})
    enjoy = taste.get("enjoy", T.leisure_prior.get(act, 0.1) if isinstance(T.leisure_prior, dict) else 0.1)
    n = taste.get("n", 0)
    # unknown things are a bit attractive to a curious mind
    curiosity_bonus = 0.25 * math.exp(-n / 2.0) * min(1.0, s.drives.get("novelty", 0.0) / 0.6)
    a = max(0.05, 0.5 + 0.9 * enjoy) + curiosity_bonus
    # done a lot lately -> wears off (and the last session's taste lingers)
    recent = sum(math.exp(-(now - e["end"]) * LN2 / (T.leisure_satiety_h * 3600.0))
                 for e in lz["log"] if e["activity"] == act)
    a /= 1.0 + T.leisure_satiety * recent
    cur = lz.get("current")
    if cur and cur["activity"] == act and cur["status"] == "paused":
        a += T.leisure_resume_bonus * (0.5 + 0.5 * max(0.0, cur.get("enjoy_so_far", 0.0) + 0.5))
    # activity / mood fit
    if act == "code":
        a *= 0.6 + 0.6 * max(0.0, s.mood["p"] + 0.3)     # needs a decent mood
    # energy: tiredness makes demanding things unappealing, loved ones much less so
    a *= max(0.0, 1.0 - 1.4 * effort_cost(engine, s, act))
    if act == "journal":
        a *= 1.0 + 0.6 * s.drives.get("sleepiness", 0.0)   # winding down before sleep
    if act == "journal":
        import time as _t
        hr = _t.localtime(now).tm_hour
        a *= 1.6 if (hr >= 20 or hr < 2) else 0.4     # an evening thing
        if sessions_since(lz, now, 18, "journal"):
            a *= 0.05                                   # already wrote today
        if lz["log"] and lz["log"][-1]["activity"] != "journal" and now - lz["log"][-1]["end"] < 6 * 3600:
            a *= 1.2  # something to write about
    return max(0.0, a)


# --------------------------------------------------------------------------
# Decision
# --------------------------------------------------------------------------

def decide(engine, s, now: float, available: list[str] | None = None,
           rng: random.Random | None = None, force: bool = False) -> dict[str, Any]:
    """Should the agent start something now, and what? Never starts anything
    itself: returns {"start": act|None, "reason": ..., ...}."""
    T = engine.t
    lz = ensure(s)
    rng = rng or random.Random()
    if available is not None:
        lz["available"] = {a: True for a in available}
    avail = [a for a in (available if available is not None else list(lz["available"]) or list(ACTIVITIES))
             if a in ACTIVITIES and a in T.leisure_activities]
    out: dict[str, Any] = {"start": None, "urge": round(urge(engine, s), 3),
                           "gate": round(mood_gate(engine, s), 3)}
    cur = lz.get("current")
    if not T.leisure_enabled:
        return {**out, "reason": "disabled"}
    if cur and cur["status"] == "running":
        return {**out, "reason": "busy", "current": cur}
    update_rest(engine, s)
    if cur and cur["status"] == "paused" and cur.get("resume_requested"):
        return {**out, "start": cur["activity"], "resume": True, "reason": "asked to continue"}
    idle = now - max(s.last_interaction, (s.thread or {}).get("last_agent", 0.0) or 0.0)
    if not force:
        if idle < T.leisure_idle_min * 60:
            return {**out, "reason": "in a conversation"}
        if now - lz["last_end"] < T.leisure_gap_min * 60:
            return {**out, "reason": "just finished something"}
        if sessions_since(lz, now, 24) >= T.leisure_max_per_day:
            return {**out, "reason": "enough for today"}
    if not avail:
        return {**out, "reason": "nothing available"}
    u = urge(engine, s) if not force else max(0.5, urge(engine, s))
    if u < T.leisure_urge_min:
        return {**out, "reason": "not bored"}
    g = mood_gate(engine, s)
    # chance per evaluation (called about once a minute): an urge, not a timer
    # deep in the night she is asleep in all but name: (1 - sleepiness)^2
    sl = s.drives.get("sleepiness", 0.0)
    p_start = min(1.0, T.leisure_eagerness * u * g * (1 - sl) ** 2) if not force else 1.0
    roll = rng.random()
    out["p_start"] = round(p_start, 3)
    if roll >= p_start:
        return {**out, "reason": "no mood for anything" if g < 0.5 else "not yet"}
    scores = {a: appeal(engine, s, a, now) for a in avail}
    # "doing nothing" competes too; it weighs more when the mood is low
    scores["_nothing"] = (T.leisure_nothing_weight * (0.3 + 2.5 * (1 - min(1.0, g)) ** 2
                                                      + 1.5 * s.drives.get("sleepiness", 0.0)
                                                      + 1.2 * s.drives.get("fatigue", 0.0))
                          if not force else 0.0)
    out["scores"] = {k: round(v, 3) for k, v in scores.items()}
    total = sum(scores.values())
    if total <= 0:
        return {**out, "reason": "nothing appeals"}
    # softmax-ish with temperature: mostly the favourite, sometimes something else
    w = {k: v ** (1.0 / max(T.leisure_randomness, 0.05)) for k, v in scores.items()}
    r = rng.random() * sum(w.values())
    pick = None
    for k, v in w.items():
        r -= v
        if r <= 0:
            pick = k
            break
    pick = pick or max(scores, key=lambda k: scores[k])
    if pick == "_nothing":
        return {**out, "reason": "too tired for anything" if tiredness(s) > 0.55 else "chose to do nothing"}
    resume = bool(cur and cur["status"] == "paused" and cur["activity"] == pick)
    return {**out, "start": pick, "resume": resume,
            "reason": "bored" if u > 0.5 else "a bit restless"}


def start(engine, s, act: str, now: float, resume: bool = False, topic: str | None = None) -> dict:
    lz = ensure(s)
    cur = lz.get("current")
    if cur and cur["status"] == "running":
        raise ValueError("already doing something")
    if resume and cur and cur["status"] == "paused" and cur["activity"] == act:
        cur["status"] = "running"
        cur["resumed"] = now
        cur["resume_requested"] = False
        cur["announced"] = False
        return cur
    if cur and cur["status"] == "paused":
        _close(engine, s, cur, now, abandoned=True)
    lz["seq"] += 1
    cur = lz["current"] = {
        "id": lz["seq"], "activity": act, "status": "running", "started": now,
        "topic": topic, "bookmark": None, "steps": 0, "mood_start": s.mood["p"],
        "val_sum": 0.0, "val_n": 0, "enjoy_so_far": 0.0, "notes": [],
        "announced": False, "resume_requested": False}
    s.drives["novelty"] = max(0.0, s.drives["novelty"] - 0.05)
    return cur


def experience(engine, s, effects_list: list[dict], aps: list, now: float,
               bookmark: Any = None, note: str | None = None) -> dict:
    """Book-keep one step of the activity (the appraisals were already felt)."""
    lz = ensure(s)
    cur = lz.get("current")
    if not cur:
        raise ValueError("no activity")
    spec = ACTIVITIES[cur["activity"]]
    cur["steps"] += 1
    if bookmark is not None:
        cur["bookmark"] = bookmark
    if note:
        cur["notes"] = (cur["notes"] + [str(note)[:300]])[-12:]
    for ap, eff in zip(aps, effects_list):
        e = (eff or {}).get("effective_intensity", 0.0)
        cur["val_sum"] += ap.valence * (0.3 + e)
        cur["val_n"] += 1
    # a quiet step (a transition scene, a page with nothing striking) neither helps nor hurts
    cur["enjoy_so_far"] = round(cur["val_sum"] / max(1, cur["val_n"]), 4)
    if cur["activity"] == "journal":
        # writing it down: negative feelings ease a little, the good ones are savoured
        for e in s.emotions.values():
            if e.get("sustained"):
                continue
            from .emotions import EMOTIONS
            if EMOTIONS[e["name"]].valence < 0:
                e["i"] *= 1 - engine.t.journal_relief
    d = s.drives
    # flow: a gripping thing tires less, a dull one more; sleepiness makes everything heavier
    flow = max(-1.0, min(1.0, cur["enjoy_so_far"] * 2))
    cost = spec["fatigue"] * (1 - 0.6 * engine.t.flow_relief * flow) * (1 + 0.5 * d.get("sleepiness", 0.0))
    d["fatigue"] = min(1.0, d["fatigue"] + max(0.0, cost))
    s.fatigue_src["leisure"] = s.fatigue_src.get("leisure", 0.0) + max(0.0, cost)
    # switching off: something enjoyable eases the load of work and people
    if cur["enjoy_so_far"] > 0.15:
        relief = engine.t.leisure_restores * min(1.0, cur["enjoy_so_far"] / 0.5)
        for src in ("work", "social"):
            take = min(relief, s.fatigue_src.get(src, 0.0))
            if take > 0:
                s.fatigue_src[src] -= take
                d["fatigue"] = max(0.0, d["fatigue"] - take)
                relief -= take
    d["novelty"] = max(0.0, d["novelty"] - spec["novelty"] * (0.5 + 0.5 * max(0.0, cur["enjoy_so_far"] + 0.5)))
    engine._sustain(s, "boredom", d["novelty"], now)
    cur["last_step"] = now
    return cur


def should_continue(engine, s, now: float) -> dict:
    T = engine.t
    lz = ensure(s)
    cur = lz.get("current")
    if not cur:
        return {"continue": False, "reason": "no activity"}
    if cur["status"] != "running":
        return {"continue": False, "reason": cur["status"]}
    run_min = (now - cur.get("resumed", cur["started"])) / 60.0
    # keep going while wanting it outweighs what it costs: no fixed "tired" line
    sl = s.drives.get("sleepiness", 0.0)
    cost = 1.3 * effort_cost(engine, s, cur["activity"], cur["enjoy_so_far"])
    if cur["activity"] != "journal":
        cost += 0.6 * max(0.0, sl - 0.4)              # eyes closing
    want = 0.25 + 1.0 * cur["enjoy_so_far"] + 0.5 * urge(engine, s)
    cur["want"], cur["cost"] = round(want, 3), round(cost, 3)
    if cur["steps"] >= 2 and want < cost:
        return {"continue": False, "reason": "sleepy" if sl > 0.5 else "tired" if tiredness(s) > 0.4 else "lost interest"}
    if run_min >= min(T.leisure_max_minutes, ACTIVITIES.get(cur["activity"], {}).get("max_min", 999)):
        return {"continue": False, "reason": "time is up"}
    if s.mood["p"] < -0.3 and cur["mood_start"] - s.mood["p"] > 0.15:
        return {"continue": False, "reason": "it's bringing me down"}
    if cur["steps"] >= 3 and cur["enjoy_so_far"] < -0.15:
        return {"continue": False, "reason": "not enjoying it"}
    if cur["val_n"] >= 12 and cur["enjoy_so_far"] < 0.03:
        return {"continue": False, "reason": "it isn't grabbing me"}
    if cur["steps"] >= 2 and urge(engine, s) < 0.05 and cur["enjoy_so_far"] < 0.15:
        return {"continue": False, "reason": "not bored any more, and it's not that great"}
    return {"continue": True, "reason": "still into it"}


def pause(s, now: float, by: str | None = None) -> dict | None:
    """Someone started talking: put it down, keep the bookmark."""
    lz = ensure(s)
    cur = lz.get("current")
    if cur and cur["status"] == "running":
        cur["status"] = "paused"
        cur["paused"] = now
        cur["paused_by"] = by
        cur["announced"] = False
        return cur
    return None


def request_resume(s, now: float) -> dict:
    lz = ensure(s)
    cur = lz.get("current")
    if not cur or cur["status"] != "paused":
        raise ValueError("nothing paused")
    cur["resume_requested"] = True
    cur["resume_requested_t"] = now
    return cur


def finish(engine, s, now: float, rating: float | None = None, summary: str | None = None,
           reason: str | None = None) -> dict:
    lz = ensure(s)
    cur = lz.get("current")
    if not cur:
        raise ValueError("no activity")
    return _close(engine, s, cur, now, rating=rating, summary=summary, reason=reason)


def _close(engine, s, cur: dict, now: float, rating: float | None = None, summary: str | None = None,
           reason: str | None = None, abandoned: bool = False) -> dict:
    T = engine.t
    lz = ensure(s)
    felt = cur["val_sum"] / max(1, cur["val_n"])
    mood_shift = s.mood["p"] - cur["mood_start"]
    enjoy = 0.55 * felt + 0.25 * max(-1.0, min(1.0, 3 * mood_shift))
    if rating is not None:
        enjoy += 0.2 * max(-1.0, min(1.0, float(rating)))
    else:
        enjoy /= 0.8
    enjoy = max(-1.0, min(1.0, enjoy))
    if cur["steps"] == 0:
        enjoy *= 0.3
    act = cur["activity"]
    taste = lz["taste"].setdefault(act, {"enjoy": T.leisure_prior.get(act, 0.1) if isinstance(T.leisure_prior, dict) else 0.1, "n": 0})
    k = T.leisure_taste_rate * (0.4 if abandoned else 1.0)
    taste["enjoy"] = round((1 - k) * taste["enjoy"] + k * enjoy, 4)
    taste["n"] = taste.get("n", 0) + 1
    taste["last"] = now
    bm = cur.get("bookmark") if isinstance(cur.get("bookmark"), dict) else {}
    entry = {"id": cur["id"], "activity": act, "start": cur["started"], "end": now,
             "steps": cur["steps"], "enjoy": round(enjoy, 3), "rating": rating,
             "topic": cur.get("topic") or bm.get("topic") or bm.get("slug"), "summary": (summary or "")[:400],
             "reason": "abandoned" if abandoned else (reason or "done"),
             "bookmark": cur.get("bookmark")}
    lz["log"] = (lz["log"] + [entry])[-40:]
    lz["current"] = None
    lz["last_end"] = now
    return entry


def prompt_lines(engine, s, lang: str = "hu") -> list[str]:
    """What the agent should know in a conversation about her free time."""
    lz = ensure(s)
    hu = lang == "hu"
    cur = lz.get("current")
    out = []
    if cur and cur["status"] == "paused" and s.t - cur.get("paused", s.t) < 6 * 3600:
        bm = cur.get("bookmark") if isinstance(cur.get("bookmark"), dict) else {}
        topic = cur.get("topic") or bm.get("topic") or bm.get("slug")
        what = label(cur["activity"], lang) + (f" ({topic})" if topic else "")
        if not cur.get("announced"):
            out.append(("Épp a szabadidődben ezzel foglalkoztál: {w}, de félretetted, mert írtak neked. "
                        "Ha természetes, röviden mondd meg, hogy ezt csináltad, de most itt vagy.").format(w=what)
                       if hu else
                       ("You were just spending free time on: {w}, and put it down because someone wrote. "
                        "If it fits, say briefly that's what you were doing, but you're here now.").format(w=what))
        else:
            out.append(("Félretett szabadidős tevékenységed: {w}." if hu else "Put-aside free-time activity: {w}.").format(w=what))
        out.append(("Ha azt kérik, hogy folytasd, futtasd: `limbic-leisure resume`, és a háttérben folytatod. "
                    "Magadtól beszélgetés közben ne vedd elő.") if hu else
                   ("If they ask you to carry on, run `limbic-leisure resume`; it continues in the background. "
                    "Don't pick it up on your own during a conversation."))
    elif cur and cur["status"] == "running":
        out.append(("Most épp ezzel töltöd a szabadidődet: {w}." if hu else "Right now you are: {w}.")
                   .format(w=label(cur["activity"], lang)))
    recent = [e for e in lz["log"] if s.t - e["end"] < 24 * 3600][-3:]
    if recent:
        bits = []
        for e in recent:
            w = "élvezted" if e["enjoy"] > 0.25 else "elment" if e["enjoy"] > -0.1 else "nem tetszett"
            if not hu:
                w = "enjoyed it" if e["enjoy"] > 0.25 else "it was ok" if e["enjoy"] > -0.1 else "didn't like it"
            bits.append(f"{label(e['activity'], lang)}" + (f" ({e['topic']})" if e.get("topic") else "") + f": {w}")
        out.append(("Mostanában szabadidődben: " if hu else "Lately in your free time: ") + "; ".join(bits) + ".")
        last = recent[-1]
        if last.get("summary"):
            out.append(("A legutóbbiról ezt gondoltad: " if hu else "About the last one you thought: ")
                       + last["summary"][:220])
    return out
