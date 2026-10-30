"""The deterministic emotion engine.

Layers (fast -> slow):
  emotions    minutes..hours  specific, usually about someone/something
  mood        hours..days     PAD vector; pulled by emotions + baseline
  baseline    weeks           background mood; drifts towards lived mood,
                              pulled back to the temperament set point
  temperament fixed           see temperament.py

Plus drives (the 'hormones'): social contact, novelty, competence, fatigue.
Deficits grow on their own with time and give rise to loneliness/boredom.
Plus relationships: per-entity affection / trust / resentment / fear.

All time handling is explicit (`now` in unix seconds) so the engine is
fully deterministic and testable.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any

from . import intents as intents_mod
from . import needs, thread
from .appraisal import Appraisal, map_emotions
from .emotions import EMOTIONS
from .temperament import Temperament

AXES = ("p", "a", "d")
LN2 = math.log(2)
SUMMATION = 0.35
SUMMATION_BY_EMOTION = {"desire": 0.75}   # desire escalates over a conversation
DRIVES = ("social", "novelty", "competence", "fatigue", "recognition", "intimacy", "libido", "sleepiness")
FATIGUE_SOURCES = ("work", "social", "leisure", "other")


def circadian(T, now: float) -> float:
    """Sleepiness from the time of day: rises before bedtime, peaks in the small
    hours, gone an hour after waking. 0..1 (times circadian_strength)."""
    import time as _time
    if not T.circadian_strength:
        return 0.0
    try:
        bed, wake = int(T.sleep_hours[0]), int(T.sleep_hours[1])
    except (TypeError, ValueError, IndexError):
        bed, wake = 23, 7
    lt = _time.localtime(now)
    h = lt.tm_hour + lt.tm_min / 60.0
    m = (h - bed) % 24                      # hours since bedtime
    night = (wake - bed) % 24 or 8
    peak = 0.4 * night
    if m >= 22:                             # the two hours before bed
        v = 0.5 * (m - 22) / 2
    elif m < peak:
        v = 0.5 + 0.35 * m / peak
    elif m < night:
        v = 0.85 - 0.75 * (m - peak) / (night - peak)
    elif m < night + 1:                     # first hour after waking
        v = 0.1 * (1 - (m - night))
    else:
        v = 0.0
    return max(0.0, min(1.0, v * T.circadian_strength))


def tiredness(s) -> float:
    return max(s.drives.get("fatigue", 0.0), s.drives.get("sleepiness", 0.0))


def clamp(x: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def clamp01(x: float) -> float:
    return clamp(x, 0.0, 1.0)


# --------------------------------------------------------------------------
# Labels
# --------------------------------------------------------------------------

# (upper bound of pleasure, english, hungarian)
MOOD_LADDER = [
    (-0.75, "anguished", "gyötrő depresszió"),
    (-0.45, "depressed", "lehangolt, depressziós"),
    (-0.20, "sad", "szomorú"),
    (-0.07, "glum", "kedvetlen"),
    (0.07, "neutral", "passzív"),
    (0.25, "content", "elégedett"),
    (0.50, "cheerful", "jókedvű"),
    (0.75, "happy", "vidám"),
    (9.99, "elated", "kicsattanó"),
]
HYSTERESIS = 0.03


def ladder_index(p: float) -> int:
    for i, (hi, _, _) in enumerate(MOOD_LADDER):
        if p <= hi:
            return i
    return len(MOOD_LADDER) - 1


def ladder_label(i: int, lang: str = "en") -> str:
    return MOOD_LADDER[i][2 if lang == "hu" else 1]


def energy_label(a: float, p: float, lang: str = "en") -> str:
    if a > 0.35:
        return ("feszült" if p < 0 else "pörgős") if lang == "hu" else ("tense" if p < 0 else "energetic")
    if a < -0.35:
        return ("fáradt, tompa" if p < 0 else "nyugodt") if lang == "hu" else ("drained" if p < 0 else "calm")
    return "kiegyensúlyozott" if lang == "hu" else "steady"


# --------------------------------------------------------------------------
# State
# --------------------------------------------------------------------------

def _pad(p: float = 0.0, a: float = 0.0, d: float = 0.0) -> dict[str, float]:
    return {"p": p, "a": a, "d": d}


NON_ENTITIES = {"image", "images", "picture", "photo", "file", "files", "kép", "képek", "fotó", "fájl",
                "system", "rendszer", "tool", "tools", "task", "feladat", "request", "kérés", "edit",
                "user", "felhasználó", "nobody", "none", "null", "unknown", "self", "me", "én"}


def _is_entity(name: str, rels: dict) -> bool:
    """A feeling can be aimed at someone (or a named thing), not at a generic noun."""
    n = str(name).strip()
    if not n:
        return False
    if rels.get(n, {}).get("is_person"):
        return True
    if "_" in n or "/" in n:  # snake_case keys and paths are things, not someone
        return False
    return n.lower() not in NON_ENTITIES


@dataclass
class State:
    t: float = 0.0
    created: float = 0.0
    mood: dict[str, float] = field(default_factory=_pad)
    baseline: dict[str, float] = field(default_factory=_pad)
    emotions: dict[str, dict[str, Any]] = field(default_factory=dict)
    drives: dict[str, float] = field(default_factory=lambda: {k: 0.0 for k in DRIVES})
    relationships: dict[str, dict[str, float]] = field(default_factory=dict)
    habituation: dict[str, float] = field(default_factory=dict)
    mood_index: int = ladder_index(0.0)
    events: int = 0
    last_consolidation: float = 0.0
    last_interaction: float = 0.0
    need_since: dict[str, float] = field(default_factory=dict)
    thread: dict[str, Any] | None = None
    intents: list[dict[str, Any]] = field(default_factory=list)
    intent_seq: int = 0
    outreach_log: list[float] = field(default_factory=list)
    reply_tracking: bool = False   # host reports agent replies (note_reply)
    leisure: dict[str, Any] = field(default_factory=dict)
    fatigue_src: dict[str, float] = field(default_factory=dict)   # what the exhaustion came from
    sexuality: dict[str, float] = field(default_factory=dict)     # learned openness to intimacy

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "State":
        s = cls()
        for k, v in (d or {}).items():
            if hasattr(s, k):
                setattr(s, k, v)
        for k in DRIVES:
            s.drives.setdefault(k, 0.0)
        for r in s.relationships.values():
            needs.normalize_relationship(r, s.t)
        return s


_new_relationship = needs.new_relationship


# --------------------------------------------------------------------------
# Engine
# --------------------------------------------------------------------------

class Engine:
    STEP_S = 300.0
    MAX_STEPS = 800

    def __init__(self, temperament: Temperament | None = None) -> None:
        self.t = temperament or Temperament()

    # ---- lifecycle -------------------------------------------------------
    def new_state(self, now: float) -> State:
        sp = dict(self.t.set_point)
        s = State(t=now, created=now, mood=dict(sp), baseline=dict(sp),
                  last_consolidation=now, last_interaction=now)
        s.mood_index = ladder_index(s.mood["p"])
        return s

    # ---- time ------------------------------------------------------------
    def advance(self, s: State, now: float) -> None:
        dt = now - s.t
        if dt <= 0:
            return
        n = min(max(1, math.ceil(dt / self.STEP_S)), self.MAX_STEPS)
        h = dt / n
        for i in range(n):
            self._step(s, h, s.t + (i + 1) * h)
        s.t = now
        self._update_label(s)

    def _step(self, s: State, h: float, now: float) -> None:
        T = self.t
        hours = h / 3600.0
        dormant = (T.dormant_after_h > 0 and s.last_interaction
                   and now - s.last_interaction > T.dormant_after_h * 3600.0)

        # 1. emotions decay; strong peaks fade faster (peak_fade)
        for key in list(s.emotions):
            e = s.emotions[key]
            spec = EMOTIONS[e["name"]]
            if e.get("sustained"):
                continue
            hl = spec.half_life_min * 60.0 / max(T.recovery, 1e-6)
            lam = LN2 / hl * (1.0 + T.peak_fade * e["i"])
            e["i"] *= math.exp(-lam * h)
            if e["i"] < 0.01:
                del s.emotions[key]

        # 2. drives grow on their own
        #    (unless the agent is dormant: nobody is using it, time is not lived)
        d = s.drives
        if not dormant:
            d["social"] += (1 - d["social"]) * (1 - math.exp(-T.social_need * hours))
            d["novelty"] += (1 - d["novelty"]) * (1 - math.exp(-T.novelty_need * hours))
            d["competence"] += (1 - d["competence"]) * (1 - math.exp(-T.competence_need * hours))
        kf_rec = math.exp(-hours * LN2 / T.fatigue_recovery_h)
        d["fatigue"] *= kf_rec
        for k in list(s.fatigue_src):
            s.fatigue_src[k] *= kf_rec
            if s.fatigue_src[k] < 0.005:
                del s.fatigue_src[k]
        d["sleepiness"] = circadian(T, now)

        # 3. unmet needs become sustained feelings (which dull when chronic)
        if dormant:
            for name in ("loneliness", "boredom"):
                if name in s.emotions:
                    s.emotions[name]["sustained"] = False
        else:
            self._sustain(s, "loneliness", d["social"], now)
            self._sustain(s, "boredom", d["novelty"], now)

        # 4. mood follows (baseline + emotions), lagged
        target = self.mood_target(s)
        k = 1 - math.exp(-h / (T.mood_tau_h * 3600.0))
        for ax in AXES:
            s.mood[ax] = clamp(s.mood[ax] + (target[ax] - s.mood[ax]) * k)

        # 5. baseline drifts towards lived mood, resilience pulls to set point
        kb = 1 - math.exp(-h / (T.baseline_tau_d * 86400.0))
        kr = 1 - math.exp(-h / (T.resilience_tau_d * 86400.0))
        for ax in AXES:
            b = s.baseline[ax]
            b += (s.mood[ax] - b) * kb + (T.set_point[ax] - b) * kr
            s.baseline[ax] = clamp(b, T.baseline_min, T.baseline_max)

        # 6. habituation wears off
        kh = math.exp(-h * LN2 / (T.habituation_half_life_h * 3600.0))
        for key in list(s.habituation):
            s.habituation[key] *= kh
            if s.habituation[key] < 0.05:
                del s.habituation[key]

        # 7. needs layer (attachment, recognition, intimacy, desire) + thread
        needs.step(self, s, h, now, bool(dormant))
        thread.step(self, s, h, now)

        # 8. relationships: grudges and fear fade in days, bonds in months
        kf = math.exp(-h * LN2 / (3 * 86400.0))
        ks = math.exp(-h * LN2 / (90 * 86400.0))
        for r in s.relationships.values():
            r["resentment"] *= kf
            r["fear"] *= kf
            r["affection"] *= ks
            r["trust"] *= ks
            if r.get("strain"):
                r["strain"] *= math.exp(-h * LN2 / (12 * 3600.0))

    def mood_target(self, s: State) -> dict[str, float]:
        """Where mood is heading: background + active emotions (+ fatigue)."""
        T = self.t
        push = {ax: 0.0 for ax in AXES}
        for e in s.emotions.values():
            pad = EMOTIONS[e["name"]].pad
            for ax, v in zip(AXES, pad):
                push[ax] += T.emotion_gain * e["i"] * v
        push["a"] -= 0.5 * s.drives["fatigue"] + 0.4 * s.drives.get("sleepiness", 0.0)
        # soft saturation: the extremes need several strong feelings at once
        return {ax: clamp(s.baseline[ax] + push[ax] / (1 + 0.5 * abs(push[ax]))) for ax in AXES}

    def _sustain(self, s: State, name: str, deficit: float, now: float) -> None:
        T = self.t
        thr = T.need_threshold
        key = name
        if deficit > thr:
            since = s.need_since.setdefault(name, now)
            chronic = (now - since) / 86400.0
            adapt = 0.3 + 0.7 * math.exp(-chronic * LN2 / max(T.need_adaptation_d, 1e-6))
            level = T.need_feeling_max * (deficit - thr) / (1 - thr) * adapt
            e = s.emotions.get(key)
            if e is None:
                s.emotions[key] = {"name": name, "target": None, "i": level,
                                   "cause": "unmet need", "sustained": True}
            else:
                e["i"] = max(e["i"], level) if not e.get("sustained") else level
                e["sustained"] = True
        else:
            s.need_since.pop(name, None)
            e = s.emotions.get(key)
            if e is not None and e.get("sustained"):
                e["sustained"] = False  # starts decaying normally

    def _update_label(self, s: State) -> None:
        p = s.mood["p"]
        i = s.mood_index
        lo = MOOD_LADDER[i - 1][0] if i > 0 else -9.99
        hi = MOOD_LADDER[i][0]
        if p > hi + HYSTERESIS or p < lo - HYSTERESIS:
            s.mood_index = ladder_index(p)

    # ---- events ----------------------------------------------------------
    def apply(self, s: State, ap: Appraisal, now: float, speaker: str | None = None,
              private: bool = False) -> dict[str, Any]:
        self.advance(s, now)
        T = self.t
        rel = None
        if speaker:  # whoever talks to the agent is a person
            sp = s.relationships.setdefault(speaker, _new_relationship(now))
            needs.normalize_relationship(sp, now)
            sp["is_person"] = True
        if ap.target and not _is_entity(ap.target, s.relationships):
            ap.target = None
        if ap.target:
            rel = s.relationships.get(ap.target)
            if ap.fiction and rel is not None and not rel.get("fictional"):
                # a character never borrows a real person's relationship
                ap.target = f"{ap.target} (story)"
                rel = s.relationships.get(ap.target)
            if rel is None:
                rel = s.relationships[ap.target] = _new_relationship(now)
            if ap.fiction:
                rel["fictional"] = True
                rel["is_person"] = False
            needs.normalize_relationship(rel, now)

        # habituation: the 10th identical compliment barely registers
        hkey = f"{ap.event}|{ap.target or ''}"
        hab = s.habituation.get(hkey, 0.0)
        strength = T.habituation_strength * (T.negative_habituation if ap.valence < 0 else 1.0)
        hfac = 1.0 / (1.0 + strength * hab)
        s.habituation[hkey] = hab + 1.0

        # sensitivity: temperament, mood-congruence, relationship
        low_mood = max(0.0, -s.mood["p"])
        tired = tiredness(s)
        if ap.valence < 0:
            sens = T.negative_sensitivity * (1 + T.mood_congruence * low_mood)
            sens *= 1 + T.tired_negativity * max(0.0, tired - 0.3) / 0.7
        else:
            sens = T.positive_sensitivity * (1 - 0.5 * T.mood_congruence * low_mood)
            sens *= 1 + 0.5 * max(0.0, ap.affinity)
        if rel is not None:
            # words from people we love weigh more, praise from a foe less
            sens *= 1 + 0.5 * rel["affection"]
            if ap.valence > 0:
                sens *= 1 - 0.5 * rel["resentment"]

        eff = clamp01(ap.intensity * hfac * sens * T.reactivity * (0.5 + 0.5 * ap.relevance)
                      * (T.fiction_gain if ap.fiction else 1.0))
        if ap.affinity < 0 and ap.valence >= 0:
            # talking about something the persona dislikes is mildly unpleasant
            ap = Appraisal.from_dict({**ap.to_dict(), "valence": ap.affinity * 0.5})

        mundane = eff < T.mundane_threshold and ap.humor < 0.2
        effects: dict[str, Any] = {"effective_intensity": round(eff, 4),
                                   "habituation": round(hfac, 4),
                                   "mundane": mundane, "emotions": {}}
        mood_before = dict(s.mood)

        if mundane:
            # small talk can lift you back to baseline, never above it
            for ax in AXES:
                if ap.valence >= 0 or s.mood[ax] > s.baseline[ax]:
                    s.mood[ax] += (s.baseline[ax] - s.mood[ax]) * T.mundane_recovery * (1 if ap.valence >= 0 else 0.3)
        else:
            for name, w in map_emotions(ap, s.mood).items():
                gained = self._add_emotion(s, name, ap.target, eff * w, ap.summary or ap.event)
                effects["emotions"][name] = round(gained, 4)

        if ap.valence < -0.1 and tired > 0.45 and not mundane and not ap.fiction:
            fr = eff * 0.4 * (tired - 0.45) / 0.55
            if fr >= 0.02:
                effects["emotions"]["frustration"] = round(effects["emotions"].get("frustration", 0.0) + self._add_emotion(
                    s, "frustration", None, fr, "tired, and this on top of it"), 4)

        if ap.humor > 0:
            amuse = clamp01(ap.humor * hfac * T.reactivity * (1 + 0.5 * max(0.0, ap.affinity)) * 0.8)
            if amuse >= 0.05:
                effects["emotions"]["amusement"] = round(
                    self._add_emotion(s, "amusement", None, amuse, ap.summary or "joke"), 4)


        # drives
        d = s.drives
        if ap.interaction:
            s.last_interaction = now
            if speaker:
                from . import leisure
                if leisure.pause(s, now, speaker):
                    effects["leisure_paused"] = True
            d["social"] *= 0.6 if ap.valence >= -0.3 else 0.85
        d["novelty"] = clamp01(d["novelty"] - 0.5 * ap.novelty + 0.04 * (1 - ap.novelty) * (1 - hfac + 0.3))
        if ap.competence > 0:
            d["competence"] = clamp01(d["competence"] - 0.6 * ap.competence)
        elif ap.competence < 0:
            d["competence"] = clamp01(d["competence"] - 0.3 * ap.competence)
        if ap.effort > 0.15 or ap.competence != 0:
            src, share = "work", 1.0
        elif ap.interaction:
            src, share = "social", (T.social_fatigue if ap.valence >= -0.1 else 1.0)
        else:
            src, share = "other", T.passive_fatigue
        # flow: what engages tires less, what bores or bothers tires more
        flow = max(-1.0, min(1.0, ap.affinity + 0.5 * ap.valence + 0.5 * ap.humor))
        cost = T.fatigue_per_effort * (0.2 + ap.effort) * share * (1 - 0.6 * T.flow_relief * flow)
        cost = max(0.0, cost)
        d["fatigue"] = clamp01(d["fatigue"] + cost)
        s.fatigue_src[src] = s.fatigue_src.get(src, 0.0) + cost
        effects["fatigue_cost"] = round(cost, 4)
        for name in ("loneliness", "boredom"):
            e = s.emotions.get(name)
            if e and e.get("sustained"):
                self._sustain(s, name, d["social" if name == "loneliness" else "novelty"], now)

        # relationships
        if rel is not None:
            self._update_relationship(rel, ap, eff, effects, now)
        needs.on_event(self, s, ap, eff, rel, now, effects)
        thread.on_message(self, s, ap, now, speaker, private, effects)
        if rel is not None:
            effects["relationship"] = {k: round(rel[k], 4) for k in
                                       ("affection", "trust", "resentment", "fear", "longing", "intimacy")}
        effects["emotions"] = {k: v for k, v in effects["emotions"].items() if v}
        # immediate partial jolt towards the new target, so feelings show up in
        # the very next reply; never beyond what the active emotions justify
        if effects["emotions"]:
            target = self.mood_target(s)
            for ax in AXES:
                s.mood[ax] = clamp(s.mood[ax] + T.jolt * (target[ax] - s.mood[ax]))

        s.events += 1
        self._update_label(s)
        effects["mood_delta"] = {ax: round(s.mood[ax] - mood_before[ax], 4) for ax in AXES}
        return effects

    def _add_emotion(self, s: State, name: str, target: str | None, amount: float, cause: str) -> float:
        if amount <= 0:
            return 0.0
        spec = EMOTIONS[name]
        tgt = target if spec.targeted else None
        key = f"{name}@{tgt}" if tgt else name
        e = s.emotions.get(key)
        if e is None:
            e = s.emotions[key] = {"name": name, "target": tgt, "i": 0.0, "cause": cause}
        before = e["i"]
        # partial summation: a feeling rises to the strongest stimulus, and only
        # part of the rest adds on top (many small pokes != one huge event)
        hi, lo = max(before, amount), min(before, amount)
        e["i"] = clamp01(hi + SUMMATION_BY_EMOTION.get(name, SUMMATION) * lo * (1 - hi))
        e["cause"] = cause
        e["sustained"] = False
        return e["i"] - before

    def _update_relationship(self, rel, ap: Appraisal, eff: float, effects, now: float) -> None:
        rel["interactions"] = rel.get("interactions", 0) + 1
        rel["last_seen"] = now
        caused_by_them = ap.agency == "other"
        if ap.valence > 0:
            w = eff * ap.valence * (1.0 if caused_by_them else 0.3)
            rel["affection"] = clamp01(rel["affection"] + 0.3 * w * (1 - rel["affection"]))
            rel["trust"] = clamp(rel["trust"] + 0.2 * w * (1 - rel["trust"]))
            rel["resentment"] = clamp01(rel["resentment"] * (1 - 0.3 * w))
        elif ap.valence < 0 and not ap.rebuff:
            n = eff * -ap.valence
            if ap.loss and not caused_by_them:
                # losing someone you love: grief, not grudge
                return
            if caused_by_them:
                rel["resentment"] = clamp01(rel["resentment"] + 0.4 * n * (1 - rel["resentment"]))
                # asymptotic towards -1, cushioned by the bond, and saturating within
                # an episode: one bad evening dents trust, it does not erase months
                drop = 0.3 * n * (1 + rel["trust"]) / 2 * (1 - 0.4 * rel["affection"])
                drop /= 1 + 6 * rel.get("strain", 0.0)
                rel["trust"] = clamp(rel["trust"] - drop)
                rel["strain"] = rel.get("strain", 0.0) + drop
                if "fear" in effects["emotions"] or ap.prospective:
                    rel["fear"] = clamp01(rel["fear"] + 0.4 * n * (1 - rel["fear"]))
        # plain, pleasant contact slowly builds familiarity
        if ap.interaction and ap.valence >= 0:
            rel["affection"] = clamp01(rel["affection"] + 0.005 * (1 - rel["affection"]))

    # ---- consolidation ("sleep") -----------------------------------------
    def consolidate(self, s: State, day_events: list[dict], now: float) -> dict[str, Any]:
        """Re-live the strongest memories of the period, partly reset
        habituation and fatigue. Call it periodically (e.g. nightly cron)."""
        self.advance(s, now)
        T = self.t
        before = dict(s.baseline)
        ruminated = savored = 0
        # 1. the day's net balance ("it was a good day") moves the background a
        #    little, saturating: calibrated appraisers rarely produce strong events,
        #    but many good small things still make a good life
        net = 0.0
        for ev in day_events:
            eff = (ev.get("effects") or {}).get("effective_intensity", 0.0)
            v = (ev.get("appraisal") or {}).get("valence", 0.0) or 0.0
            if eff <= 0 or abs(v) < 0.05:
                continue
            net += eff * v * (T.savoring if v > 0 else T.rumination) / 0.35
        balance = math.tanh(net / max(T.day_balance_scale, 1e-6))
        s.baseline["p"] = clamp(s.baseline["p"] + T.day_balance_gain * balance, T.baseline_min, T.baseline_max)
        s.baseline["d"] = clamp(s.baseline["d"] + 0.5 * T.day_balance_gain * balance, T.baseline_min, T.baseline_max)
        # 2. re-live the strongest moments
        for ev in day_events:
            eff = (ev.get("effects") or {}).get("effective_intensity", 0.0)
            ap = Appraisal.from_dict(ev.get("appraisal") or {})
            if eff < 0.35:
                continue
            factor = T.rumination if ap.valence < 0 else T.savoring
            if factor <= 0:
                continue
            for name, w in map_emotions(ap, s.mood).items():
                self._add_emotion(s, name, ap.target, 0.5 * factor * eff * w,
                                  f"(memory) {ap.summary or ap.event}")
            if ap.valence < 0:
                ruminated += 1
            else:
                savored += 1
            # memory feeds the background mood directly, a little
            for ax, v in zip(AXES, _pad_of_valence(ap.valence)):
                s.baseline[ax] = clamp(s.baseline[ax] + 0.02 * factor * eff * v,
                                       T.baseline_min, T.baseline_max)
        for k in list(s.habituation):
            s.habituation[k] *= 0.5
        s.drives["fatigue"] *= 0.3
        for k in list(s.fatigue_src):
            s.fatigue_src[k] *= 0.3
        s.last_consolidation = now
        return {"ruminated": ruminated, "savored": savored, "day_balance": round(balance, 4),
                "baseline_before": before, "baseline_after": dict(s.baseline)}

    def recall(self, s: State, event: dict, now: float, times_recalled: int = 0) -> dict[str, Any]:
        """Bringing up a memory re-triggers its feeling, weaker each time."""
        self.advance(s, now)
        ap = Appraisal.from_dict(event.get("appraisal") or {})
        eff = (event.get("effects") or {}).get("effective_intensity", ap.intensity)
        factor = 0.35 * (0.6 ** times_recalled)
        out = {}
        for name, w in map_emotions(ap, s.mood).items():
            out[name] = round(self._add_emotion(s, name, ap.target, factor * eff * w,
                                                f"(remembered) {ap.summary or ap.event}"), 4)
        self._update_label(s)
        return {"emotions": out, "factor": factor}

    # ---- outputs ---------------------------------------------------------
    def emotion_level(self, s: State, name: str, target: str | None = None) -> float:
        best = 0.0
        for e in s.emotions.values():
            if e["name"] == name and (target is None or e["target"] in (None, target)):
                best = max(best, e["i"])
        return best

    def modulators(self, s: State, interlocutor: str | None = None) -> dict[str, float]:
        p, a, d = s.mood["p"], s.mood["a"], s.mood["d"]
        f = s.drives["fatigue"]
        sl = s.drives.get("sleepiness", 0.0)
        f = max(f, 0.8 * sl)
        E = lambda n, tgt=None: self.emotion_level(s, n, tgt)  # noqa: E731
        rel = s.relationships.get(interlocutor or "", {})
        anger = E("anger", interlocutor) if interlocutor else E("anger")
        return {
            "verbosity": round(clamp01(0.5 + 0.2 * p + 0.2 * a - 0.35 * f - 0.25 * E("sadness") - 0.15 * E("boredom")), 3),
            "initiative": round(clamp01(0.35 + 0.25 * p + 0.2 * a + 0.35 * s.drives["social"] * (1 if p > -0.4 else 0.3) - 0.3 * f + 0.3 * E("excitement")), 3),
            "warmth": round(clamp01(0.5 + 0.35 * p + 0.3 * rel.get("affection", 0) + 0.2 * rel.get("intimacy", 0) - 0.4 * rel.get("resentment", 0) - 0.3 * anger - 0.2 * E("longing", interlocutor) * (1 if rel.get("unanswered") else 0)), 3),
            "patience": round(clamp01(0.75 - 0.6 * anger - 0.4 * E("frustration") - 0.25 * f - 0.2 * E("boredom")), 3),
            "optimism": round(clamp01(0.5 + 0.45 * p + 0.15 * E("hope") - 0.3 * E("fear")), 3),
            "caution": round(clamp01(0.4 - 0.25 * d + 0.5 * E("fear") + 0.2 * rel.get("fear", 0)), 3),
            "playfulness": round(clamp01(0.3 + 0.3 * p + 0.15 * a + 0.5 * E("amusement") - 0.3 * E("sadness") - 0.3 * anger), 3),
            "curiosity": round(clamp01(0.4 + 0.4 * s.drives["novelty"] + 0.15 * a - 0.3 * f), 3),
            "temperature_delta": round(clamp(0.12 * a + 0.08 * E("amusement"), -0.2, 0.2), 3),
        }

    def alerts(self, s: State) -> list[str]:
        out = []
        if s.baseline["p"] < self.t.crisis_floor:
            out.append("baseline_crisis")
        if s.mood["p"] < -0.75:
            out.append("mood_anguished")
        if s.drives["social"] > 0.9:
            out.append("isolated")
        return out

    def snapshot(self, s: State, interlocutor: str | None = None) -> dict[str, Any]:
        emos = sorted(s.emotions.values(), key=lambda e: -e["i"])
        return {
            "t": s.t,
            "label": ladder_label(s.mood_index),
            "label_hu": ladder_label(s.mood_index, "hu"),
            "energy": energy_label(s.mood["a"], s.mood["p"]),
            "energy_hu": energy_label(s.mood["a"], s.mood["p"], "hu"),
            "baseline_label": ladder_label(ladder_index(s.baseline["p"])),
            "baseline_label_hu": ladder_label(ladder_index(s.baseline["p"]), "hu"),
            "mood": {k: round(v, 4) for k, v in s.mood.items()},
            "baseline": {k: round(v, 4) for k, v in s.baseline.items()},
            "emotions": [{"name": e["name"], "hu": EMOTIONS[e["name"]].hu, "target": e["target"],
                          "i": round(e["i"], 4), "cause": e.get("cause", "")} for e in emos],
            "drives": {k: round(v, 4) for k, v in s.drives.items()},
            "relationships": {k: {kk: (round(vv, 4) if isinstance(vv, float) else vv) for kk, vv in r.items()}
                              for k, r in s.relationships.items()},
            "modulators": self.modulators(s, interlocutor),
            "steering": intents_mod.steering(self, s, interlocutor, s.t),
            "heat": round(needs.heat(self, s, interlocutor), 3) if interlocutor else 0.0,
            "thread": dict(s.thread) if s.thread else None,
            "intents": [dict(i) for i in s.intents[-15:]][::-1],
            "alerts": self.alerts(s),
            "events": s.events,
            "leisure": {**_leisure_view(s), **self._leisure_signals(s)},
            "fatigue_src": {k: round(v, 4) for k, v in s.fatigue_src.items()},
            "sexuality": {k: (round(v, 4) if isinstance(v, float) else v) for k, v in (s.sexuality or {}).items()},
        }

    def _leisure_signals(self, s: State) -> dict[str, Any]:
        from . import leisure
        return {"urge": round(leisure.urge(self, s), 3), "gate": round(leisure.mood_gate(self, s), 3),
                "urge_min": self.t.leisure_urge_min, "enabled": self.t.leisure_enabled}

    def prompt_block(self, s: State, lang: str = "en", interlocutor: str | None = None) -> str:
        from .prompt import render
        return render(self, s, lang=lang, interlocutor=interlocutor)


def _leisure_view(s: State) -> dict[str, Any]:
    from . import leisure
    lz = leisure.ensure(s)
    return {"current": lz["current"], "resting": lz["resting"], "taste": lz["taste"],
            "log": lz["log"][-10:][::-1]}


def _pad_of_valence(v: float) -> tuple[float, float, float]:
    return (v, 0.2 * abs(v), 0.3 * v)
