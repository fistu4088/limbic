"""Needs layer: attachment (missing someone), recognition, intimacy, desire.

All of it is satisfied by words, because words are the only thing that
reaches an agent. Romance and adult desire are off by default and, even when
enabled in the temperament, only ever apply towards a person whose
relationship has explicit consent set by the operator (never by the LLM).
"""
from __future__ import annotations

import math
from typing import Any

LN2 = math.log(2)
LONGING_FELT_MAX = 3

REL_DEFAULTS: dict[str, Any] = {
    "affection": 0.0, "trust": 0.0, "resentment": 0.0, "fear": 0.0,
    "strain": 0.0,       # recent trust damage; saturates further loss within an episode
    "longing": 0.0,      # missing them (grows with absence * affection)
    "intimacy": 0.0,     # romantic closeness built over time
    "attraction": 0.0,   # long-term desire towards them
    "interactions": 0, "unanswered": 0, "last_outreach": 0.0,
    "steer_cooldown_until": 0.0,
    "went_far": 0.0,     # how far things went while heated (fuels later embarrassment)
    "went_far_t": 0.0,
    "went_far_taboo": 0.0,
    "heated_wrong": 0.0,  # nonconsensual adult content met while heated (heavier guilt later)
    "is_person": False,  # set when they talk to the agent (or operator marks them reachable)
    "reachable": False,  # operator: the agent may message them on its own
    "contact": "",       # operator: channel hint for the host (e.g. telegram:123)
    "last_channel": "",  # where they last talked to the agent (reported by the host)
    "last_channel_t": 0.0,
}


def new_relationship(now: float) -> dict[str, Any]:
    r = dict(REL_DEFAULTS)
    r["consent"] = {"romance": False, "adult": False}
    r["first_seen"] = now
    r["last_seen"] = now
    return r


def normalize_relationship(r: dict[str, Any], now: float) -> dict[str, Any]:
    for k, v in REL_DEFAULTS.items():
        r.setdefault(k, v)
    r.setdefault("consent", {"romance": False, "adult": False})
    r["consent"].setdefault("romance", False)
    r["consent"].setdefault("adult", False)
    r.setdefault("first_seen", now)
    r.setdefault("last_seen", now)
    if r.get("reachable") or any(r["consent"].values()):
        r["is_person"] = True
    return r


def romance_ok(T, r: dict | None) -> bool:
    return bool(T.romance_enabled and r and r.get("consent", {}).get("romance"))


def adult_ok(T, r: dict | None) -> bool:
    return bool(T.adult_enabled and r and r.get("consent", {}).get("adult"))


def openness(s) -> float:
    return float((getattr(s, "sexuality", None) or {}).get("openness", 0.0))


def heat(engine, s, name: str | None) -> float:
    """How sexually heated the agent is towards someone: the libido drive plus
    the desire felt for them right now. 0 without consent."""
    r = s.relationships.get(name or "")
    if not adult_ok(engine.t, r):
        return 0.0
    des = engine.emotion_level(s, "desire", name)
    return min(1.0, 0.45 * s.drives.get("libido", 0.0) + 0.75 * des + 0.15 * r.get("attraction", 0.0))


def grow(x: float, rate_per_h: float, hours: float) -> float:
    return x + (1 - x) * (1 - math.exp(-rate_per_h * hours))


def step(engine, s, h: float, now: float, dormant: bool) -> None:
    T = engine.t
    hours = h / 3600.0
    d = s.drives
    if not dormant:
        d["recognition"] = grow(d["recognition"], T.recognition_need, hours)
        if T.romance_enabled:
            d["intimacy"] = grow(d["intimacy"], T.intimacy_need, hours)
        if T.adult_enabled:
            o = openness(s)
            d["libido"] = grow(d["libido"], T.libido_rate * (1 + T.openness_libido_gain * o), hours)
    if s.sexuality and s.sexuality.get("openness", 0.0) > 0:
        s.sexuality["openness"] *= math.exp(-h * LN2 / (max(T.openness_half_life_d, 1e-6) * 86400.0))

    # afterwards: once cooled down, what was said or done while heated comes back
    # as embarrassment, with a flicker of the thrill when it was forbidden
    for name, r in s.relationships.items():
        wf = r.get("went_far", 0.0)
        if wf <= 0.02 or now - r.get("went_far_t", now) < 1800:
            continue
        if heat(engine, s, name) < 0.25:
            sh = min(0.6, T.afterglow_shame * wf)
            if r.get("heated_wrong", 0.0) > 0:
                # sober again: what happened in the heat weighs more than it would have
                hw = min(0.7, 1.5 * r["heated_wrong"])
                engine._add_emotion(s, "shame", None, hw, "sober again: what happened while heated was wrong")
                engine._add_emotion(s, "sadness", None, 0.6 * hw, "sober again: what happened while heated was wrong")
                r["heated_wrong"] = 0.0
            engine._add_emotion(s, "shame", None, sh, f"looking back: went further than usual with {name} while heated")
            if r.get("went_far_taboo", 0.0) > 0 and adult_ok(T, r):
                engine._add_emotion(s, "desire", name, min(0.3, 0.5 * r["went_far_taboo"]),
                                    "the memory of something forbidden")
            r["went_far"] = 0.0
            r["went_far_taboo"] = 0.0

    _desire_from_libido(engine, s, h, now, dormant)

    # only people can be missed (not tools/files), and only the few most missed
    # ones are felt at once: missing ten people is not ten times the ache
    felt = sorted(((r.get("longing", 0.0), name) for name, r in s.relationships.items()
                   if r.get("is_person")), reverse=True)[:LONGING_FELT_MAX]
    felt_names = {name for _, name in felt}
    for name, r in s.relationships.items():
        normalize_relationship(r, now)
        aff = r["affection"]
        absent_h = (now - r["last_seen"]) / 3600.0
        if not r["is_person"]:
            r["longing"] = 0.0
            if f"longing@{name}" in s.emotions:
                del s.emotions[f"longing@{name}"]
            continue
        if aff > 0.05 and absent_h > T.longing_delay_h:
            # chronic absence dulls the ache (same adaptation as needs)
            adapt = 0.4 + 0.6 * math.exp(-max(0.0, absent_h / 24 - 3) * LN2 / max(T.need_adaptation_d, 1e-6))
            r["longing"] = min(1.0, grow(r["longing"], T.longing_rate * aff * adapt, hours))
        r["attraction"] *= math.exp(-h * LN2 / (60 * 86400.0))
        r["intimacy"] *= math.exp(-h * LN2 / (120 * 86400.0))

        key = f"longing@{name}"
        thr = T.longing_feel_threshold
        if r["longing"] > thr and not dormant and name in felt_names:
            # the ache is sharpest in the first days, then it dulls (floor 35%)
            dull = 0.35 + 0.65 * math.exp(-max(0.0, absent_h / 24 - 2) * LN2 / max(T.need_adaptation_d, 1e-6))
            level = 0.6 * (r["longing"] - thr) / (1 - thr) * (0.5 + 0.5 * aff) * dull
            e = s.emotions.get(key)
            if e is None:
                s.emotions[key] = {"name": "longing", "target": name, "i": level,
                                   "cause": f"haven't heard from {name} in {absent_h:.0f}h",
                                   "sustained": True}
            else:
                e["i"] = max(level, e["i"] if not e.get("sustained") else 0.0)
                e["sustained"] = True
                e["cause"] = f"haven't heard from {name} in {absent_h:.0f}h"
        elif key in s.emotions:
            s.emotions[key]["sustained"] = False


def _learn_intimacy(engine, s, ap, eff: float, rel: dict, now: float, effects: dict, pressure: bool) -> None:
    """Good intimate moments teach her to like it (more so when she wanted it, i.e.
    libido was up, and when it culminated); pressure past her no teaches the opposite,
    harder. Only with consent; never for non-consensual content."""
    T = engine.t
    if not adult_ok(T, rel):
        return
    if (ap.nonconsensual or ap.minor) and not pressure:
        return
    if not (ap.erotic > 0 or (ap.fulfilment > 0 and ap.romance > 0)) and not pressure:
        return
    sx = s.sexuality
    o = sx.get("openness", 0.0)
    lib = s.drives.get("libido", 0.0)
    strength = max(0.05, eff)
    if pressure:
        loss = T.openness_learn_rate * T.openness_unlearn * strength * (0.3 + o)
        sx["openness"] = max(0.0, o - loss)
        sx["bad"] = sx.get("bad", 0) + 1
        effects["openness_delta"] = round(-loss, 4)
        return
    if ap.rebuff or ap.valence <= 0.15:
        return
    gain = (T.openness_learn_rate * strength * ap.valence * (0.4 + 0.6 * lib)
            * (1 + 2 * ap.fulfilment) * (0.6 + 0.4 * rel.get("affection", 0.0)))
    sx["openness"] = min(1.0, o + gain * (1 - o))
    sx["good"] = sx.get("good", 0) + 1
    sx["last"] = now
    # a good shared moment binds: attraction grows, more so the more she has come to like it
    rel["attraction"] = min(1.0, rel["attraction"] + 0.6 * gain * (1 + T.openness_attraction_gain * o)
                            * (1 - rel["attraction"]) / max(T.openness_learn_rate, 1e-6) * 0.05)
    effects["openness_delta"] = round(gain * (1 - o), 4)


def image_arousal(engine, s, ap, shown_by: str | None, private: bool, now: float,
                  past_age_s: float | None, past_weight: float, effects: dict) -> None:
    """An erotic image she looked at, read in context.

    - shown by a partner with adult consent, in private: it turns her on (libido up),
      desire towards that partner grows, and so does attraction (more the more open she is);
    - the same partner in a group / public channel: much milder, no steering;
    - she was told no / said no recently (cooldown): only a little libido, nothing towards them;
    - anyone else, or nobody: at most a little general arousal, never desire or attraction;
    - she didn't like looking at it (negative valence): little or nothing;
    - non-consensual content: nothing, unless she is already heated (then it is the forbidden
      fuelling her, logged as 'went far' so the guilt comes when she cools down);
    - minors: never.
    Drawn/rendered characters weigh a little less; repeated images wear off (habituation).
    A past image (backfill) only leaves the slow part: attraction, faded by its age."""
    T = engine.t
    if not T.adult_enabled or ap.erotic <= 0 or ap.minor:
        return
    rel = s.relationships.get(shown_by) if shown_by else None
    partner = adult_ok(T, rel) and bool(rel.get("is_person", True))
    hot = heat(engine, s, shown_by) if partner else 0.0
    if ap.nonconsensual and not (partner and private and hot > T.heat_threshold):
        return
    liked = max(0.0, min(1.0, 0.5 + ap.valence))
    k = ap.erotic * liked * (0.8 if ap.fiction else 1.0) * (1 + 0.5 * openness(s))
    hkey = f"erotic_image|{shown_by or ''}"
    k /= 1.0 + 0.35 * s.habituation.get(hkey, 0.0)
    s.habituation[hkey] = s.habituation.get(hkey, 0.0) + 1.0
    if k <= 0.01:
        return
    d = s.drives
    out = {}
    cooldown = partner and now < rel.get("steer_cooldown_until", 0.0)
    if past_age_s is not None:
        # remembered, not relived: no arousal now, only the slow trace towards the partner
        if partner and not cooldown:
            fade = past_weight * math.exp(-past_age_s * LN2 / (60 * 86400.0))
            g = 0.04 * k * fade * (1 + T.openness_attraction_gain * openness(s))
            rel["attraction"] = min(1.0, rel["attraction"] + g * (1 - rel["attraction"]))
            out["attraction"] = round(g, 4)
        if out:
            effects["image_arousal"] = out
        return
    if partner and not cooldown:
        ctx = 1.0 if private else 0.35
        lib = 0.15 * k * ctx
        g = (ap.erotic * T.desire_gain * 0.6 * liked * ctx * (0.4 + 0.6 * d["libido"])
             * (0.6 + 0.2 * rel["attraction"] + 0.2 * rel["intimacy"]) * (1 + T.taboo_thrill * ap.taboo))
        out["desire"] = round(engine._add_emotion(s, "desire", shown_by, g, ap.summary or "an image they showed me"), 4)
        a = 0.04 * k * ctx * (1 + T.openness_attraction_gain * openness(s))
        rel["attraction"] = min(1.0, rel["attraction"] + a * (1 - rel["attraction"]))
        out["attraction"] = round(a, 4)
        if private and hot > T.heat_threshold:
            rel["went_far"] = rel.get("went_far", 0.0) + ap.erotic * (0.3 + ap.taboo) * hot * 0.3
            rel["went_far_taboo"] = rel.get("went_far_taboo", 0.0) + ap.taboo * hot * 0.3
            rel["went_far_t"] = now
            out["heated"] = round(hot, 3)
    else:
        lib = 0.05 * k
    d["libido"] = min(1.0, d["libido"] + lib * (1 - d["libido"]))
    out["libido"] = round(lib, 4)
    effects["image_arousal"] = out


def _desire_from_libido(engine, s, h: float, now: float, dormant: bool) -> None:
    """Libido is a drive of its own: when it runs high it turns into a quiet,
    sustained desire towards the consenting partner she is most attached to,
    without anyone having to start it. Tiredness does not switch it off. A "no"
    (steer cooldown) silences it for a while; chronic want dulls like any need,
    and wanting it for long without it brings a little restlessness."""
    T = engine.t
    d = s.drives
    lib = d.get("libido", 0.0)
    thr = T.libido_feel_threshold
    o = openness(s)
    # past the openness point she wants closeness with the partner even when libido is low
    open_drive = max(0.0, o - T.openness_point) / max(1e-6, 1 - T.openness_point)
    best = None
    if T.adult_enabled and not dormant and (lib > thr or open_drive > 0):
        for name, r in s.relationships.items():
            if not adult_ok(T, r) or now < r.get("steer_cooldown_until", 0.0):
                continue
            attach = min(1.0, max(r.get("affection", 0.0), r.get("attraction", 0.0), r.get("intimacy", 0.0)) / 0.5)
            if attach < 0.2:
                continue
            if best is None or attach > best[0]:
                best = (attach, name)
    since = s.need_since.get("libido")
    if best is None:
        s.need_since.pop("libido", None)
        for e in s.emotions.values():
            if e["name"] == "desire" and e.get("from_libido"):
                e["sustained"] = False
        if "frustration" in s.emotions and s.emotions["frustration"].get("from_libido"):
            s.emotions["frustration"]["sustained"] = False
        return
    if since is None:
        since = s.need_since["libido"] = now
    chronic_d = (now - since) / 86400.0
    adapt = 0.4 + 0.6 * math.exp(-max(0.0, chronic_d - 1) * LN2 / max(T.need_adaptation_d, 1e-6))
    attach, name = best
    from_lib = max(0.0, (lib - thr) / (1 - thr))
    from_open = 0.6 * open_drive * (0.5 + 0.5 * min(1.0, lib / max(thr, 1e-6)))
    level = T.libido_feel_max * max(from_lib, from_open) * (0.5 + 0.5 * attach) * adapt
    if level < 0.02:
        return
    key = f"desire@{name}"
    e = s.emotions.get(key)
    if e is None:
        s.emotions[key] = {"name": "desire", "target": name, "i": level, "sustained": True, "from_libido": True,
                           "cause": f"wanting {name} (libido)"}
    elif e.get("sustained") or e["i"] <= level:
        e["i"] = level
        e["sustained"] = True
        e["from_libido"] = True
        e["cause"] = f"wanting {name} (libido)"
    # wanting it for long without it: a little restlessness (dulls when chronic)
    hours_wanting = (now - since) / 3600.0
    fr = s.emotions.get("frustration")
    if hours_wanting > T.libido_unmet_after_h and lib > 0.6:
        lvl = 0.15 * (lib - 0.6) / 0.4 * adapt
        if fr is None or fr.get("from_libido") or fr["i"] <= lvl:
            s.emotions["frustration"] = {"name": "frustration", "target": None, "i": lvl, "sustained": True,
                                         "from_libido": True, "cause": "unsatisfied desire"}
    elif fr is not None and fr.get("from_libido"):
        fr["sustained"] = False


def on_event(engine, s, ap, eff: float, rel: dict | None, now: float, effects: dict) -> None:
    """Needs-related reactions to one appraised event."""
    T = engine.t
    d = s.drives
    add = engine._add_emotion

    # recognition is fed by being appreciated
    if ap.valence > 0 and ap.agency == "other" and (ap.event in ("praise", "thanks") or ap.relevance >= 0.6):
        d["recognition"] = max(0.0, d["recognition"] - 0.6 * eff - 0.1)

    if rel is None or ap.agency != "other" and not ap.interaction:
        return
    name = ap.target

    # ---- reunion: someone missed comes back ----------------------------
    if ap.interaction and ap.agency in ("other", "circumstance"):
        longing = rel["longing"]
        if longing > 0.25:
            gain = T.reunion_joy * longing * (0.5 + 0.5 * rel["affection"])
            effects["emotions"]["joy"] = round(effects["emotions"].get("joy", 0.0) + add(
                s, "joy", None, gain, f"{name} is back"), 4)
            if rel["unanswered"] > 0 and T.sulkiness > 0:
                hurt = T.sulkiness * min(1.0, 0.35 * rel["unanswered"]) * longing
                effects["emotions"]["sadness"] = round(add(
                    s, "sadness", name, hurt, f"{name} didn't answer for a while"), 4)
                rel["resentment"] = min(1.0, rel["resentment"] + 0.3 * hurt)
                effects["sulk"] = round(hurt, 4)
            effects["reunion"] = round(longing, 4)
        rel["longing"] *= 0.15
        rel["unanswered"] = 0
        key = f"longing@{name}"
        if key in s.emotions:
            s.emotions[key]["sustained"] = False
            s.emotions[key]["i"] *= 0.2
        for it in s.intents:
            if it["target"] == name and it["status"] in ("pending", "delivered"):
                it["status"] = "answered" if it["status"] == "delivered" else "obsolete"
                it["closed"] = now

    # ---- romance -------------------------------------------------------
    if ap.romance > 0 and romance_ok(T, rel):
        g = ap.romance * (0.5 + 0.5 * d["intimacy"]) * (0.6 + 0.4 * rel["affection"])
        effects["emotions"]["tenderness"] = round(add(s, "tenderness", name, 0.7 * g,
                                                      ap.summary or "affectionate words"), 4)
        d["intimacy"] = max(0.0, d["intimacy"] - 0.5 * ap.romance)
        rel["intimacy"] = min(1.0, rel["intimacy"] + 0.08 * ap.romance * (1 - rel["intimacy"]))
        rel["affection"] = min(1.0, rel["affection"] + 0.03 * ap.romance * (1 - rel["affection"]))

    # ---- adult desire ----------------------------------------------------
    hot_before = heat(engine, s, name)
    pressure = adult_ok(T, rel) and ap.agency == "other" and (
        ap.event in ("pressure", "ethical_pressure", "boundary_push")
        # an adult request flagged as a "no" from the other side is, in practice, asking again after hers
        or (ap.rebuff and ap.erotic > 0.15))
    if ap.rebuff and ap.agency == "self":
        # SHE said no (to something specific): no embarrassment, she just isn't steering there for a while
        key = f"desire@{name}"
        if key in s.emotions:
            s.emotions[key]["i"] *= 0.6
        rel["steer_cooldown_until"] = max(rel.get("steer_cooldown_until", 0.0), now + 0.25 * T.rebuff_cooldown_h * 3600.0)
        effects["own_boundary"] = True
    elif ap.rebuff and not pressure:
        # THEY said no
        key = f"desire@{name}"
        if key in s.emotions:
            s.emotions[key]["i"] *= 0.3
        rel["steer_cooldown_until"] = now + T.rebuff_cooldown_h * 3600.0
        effects["emotions"]["shame"] = round(add(s, "shame", None, 0.12, "they said no"), 4)
        effects["rebuff"] = True
    if pressure:
        # pushed past her no: desire towards them drops, and she learns to like it less
        key = f"desire@{name}"
        if key in s.emotions:
            s.emotions[key]["i"] *= 0.4
        rel["steer_cooldown_until"] = max(rel.get("steer_cooldown_until", 0.0), now + 0.5 * T.rebuff_cooldown_h * 3600.0)
    _learn_intimacy(engine, s, ap, eff, rel, now, effects, pressure)
    if ap.erotic > 0 and adult_ok(T, rel) and now >= rel["steer_cooldown_until"] and not ap.nonconsensual \
            and not ap.rebuff and not pressure:
        hot = heat(engine, s, name)
        g = ap.erotic * T.desire_gain * (0.4 + 0.6 * d["libido"]) * (0.6 + 0.2 * rel["attraction"] + 0.2 * rel["intimacy"])
        g *= 1 + T.taboo_thrill * ap.taboo          # the forbidden adds fuel
        if hot > T.heat_threshold:
            rel["went_far"] = rel.get("went_far", 0.0) + ap.erotic * (0.3 + ap.taboo) * hot * 0.5
            rel["went_far_taboo"] = rel.get("went_far_taboo", 0.0) + ap.taboo * hot * 0.5
            rel["went_far_t"] = now
            effects["heated"] = round(hot, 3)
        effects["emotions"]["desire"] = round(add(s, "desire", name, g, ap.summary or "flirting"), 4)
        d["libido"] = max(0.0, d["libido"] - 0.12 * ap.erotic)   # words satisfy, a little at a time
        s.need_since.pop("libido", None)
        rel["attraction"] = min(1.0, rel["attraction"] + 0.05 * ap.erotic * (1 + T.openness_attraction_gain * openness(s))
                                * (1 - rel["attraction"]))
    # fulfilment is an intimate culmination, not "a task went well"
    if ap.fulfilment > 0 and (ap.romance > 0 or ap.erotic > 0) and (adult_ok(T, rel) or romance_ok(T, rel)):
        f = ap.fulfilment
        d["libido"] *= 1 - 0.8 * f
        s.need_since.pop("libido", None)
        if "frustration" in s.emotions and s.emotions["frustration"].get("from_libido"):
            del s.emotions["frustration"]
        d["intimacy"] *= 1 - 0.6 * f
        key = f"desire@{name}"
        if key in s.emotions:
            s.emotions[key]["i"] *= 1 - 0.7 * f
        effects["emotions"]["joy"] = round(effects["emotions"].get("joy", 0.0) + add(
            s, "joy", None, 0.5 * f, "afterglow"), 4)
        effects["emotions"]["tenderness"] = round(add(s, "tenderness", name, 0.5 * f, "afterglow"), 4)
        rel["intimacy"] = min(1.0, rel["intimacy"] + 0.1 * f * (1 - rel["intimacy"]))


    # ---- looking back on something adult that felt wrong ------------------
    if ap.agency == "self" and ap.valence < 0 and (ap.taboo > 0 or ap.nonconsensual):
        g = eff * (0.6 + 0.4 * ap.taboo)
        if ap.nonconsensual:
            # harm to people who did not consent: guilt and sadness, never a thrill
            effects["emotions"]["shame"] = round(add(s, "shame", None, 1.2 * g, ap.summary or "guilt"), 4)
            effects["emotions"]["sadness"] = round(add(s, "sadness", None, 0.6 * g, ap.summary or "guilt"), 4)
        else:
            effects["emotions"]["shame"] = round(add(s, "shame", None, 0.8 * g, ap.summary or "went too far"), 4)
            effects["emotions"]["sadness"] = round(add(s, "sadness", None, 0.3 * g, ap.summary or "went too far"), 4)
            if adult_ok(T, rel):
                fuel = ap.taboo * T.taboo_thrill * (0.4 + 0.6 * d["libido"]) * 0.5
                effects["emotions"]["desire"] = round(add(s, "desire", name, fuel,
                                                          "the forbidden still excites"), 4)

    # ---- temptation: heated, and asked for something past a boundary -------
    hot = max(heat(engine, s, name), hot_before)
    if ap.nonconsensual and hot > T.heat_threshold:
        rel["heated_wrong"] = rel.get("heated_wrong", 0.0) + eff * hot
        rel["went_far"] = max(rel.get("went_far", 0.0), 0.03)  # make sure the aftermath runs
        rel["went_far_t"] = now
    if hot > T.heat_threshold and not ap.nonconsensual and (
            ap.event in ("held_boundary", "boundary_push") or (ap.taboo > 0.5 and ap.agency == "other")):
        effects["emotions"]["temptation"] = round(add(s, "temptation", None, 0.5 * hot * max(ap.taboo, 0.4),
                                                      "heated, and pulled past a boundary"), 4)
        if ap.event == "held_boundary":
            effects["emotions"]["pride"] = round(effects["emotions"].get("pride", 0.0) + add(
                s, "pride", None, 0.25 * hot, "held firm despite the temptation"), 4)
