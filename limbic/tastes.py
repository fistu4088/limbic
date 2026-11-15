"""Tastes: what the agent likes, dislikes and won't put up with.

Two layers, both stored per agent in state.leisure["tastes"]:

* prior: a starting guess from a seed file (data/priors/<agent>.json), low
  confidence. Experience overrides it quickly. Unexperienced priors fade.
* experience: learned from finished leisure and conversations. Repeats move
  it less (habituation); negatives count a bit more (negativity bias).

Boundaries are a kind of taste that does not fade and is set only by a clear
refusal. Hard rules (limbic/rules.py) sit above everything here and are never
written into this store.

Taste entries: {"w": -1..1 direction, "conf": 0..1, "n": count,
                "kind": "topic"|"boundary", "src": "prior"|"experience",
                "last": unix time of last experience or seeding}
"""
from __future__ import annotations

import math
import random
from typing import Any, Iterable

from . import rules

DAY = 86400.0
HALF_LIFE_D = 30.0      # unexperienced taste fades to half in this many days
BASE_RATE = 0.35        # how much one fully salient experience moves a taste
PRIOR_CONF = 0.2        # priors are weak: experience takes over fast
BOUNDARY_PRIOR_CONF = 0.5


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def norm(topic: Any) -> str:
    return str(topic or "").strip().lower()[:60]


def _store(s) -> dict[str, Any]:
    lz = getattr(s, "leisure", None)
    if not isinstance(lz, dict):
        s.leisure = lz = {}
    return lz.setdefault("tastes", {})


def seed(s, priors: list[dict], now: float) -> int:
    """Add prior tastes. Never overwrites an entry that already has experience."""
    tastes = _store(s)
    added = 0
    for p in priors or []:
        topic = norm(p.get("topic"))
        if not topic or topic in tastes:
            continue
        # Priors are always soft guesses. Even a "boundary" prior only seeds a
        # weak negative; it becomes fixed only through an explicit stop request.
        kind = "topic"
        w = _clamp(float(p.get("w", -0.3 if p.get("kind") == "boundary" else 0.3)), -1.0, 1.0)
        conf = PRIOR_CONF
        tastes[topic] = {"w": w, "conf": conf, "n": 0, "kind": kind,
                         "src": "prior", "last": now}
        added += 1
    return added


def _update(tastes: dict, topic: str, v: float, sal: float, now: float, rate_mul: float = 1.0) -> dict:
    e = tastes.get(topic) or {"w": 0.0, "conf": 0.0, "n": 0, "kind": "topic",
                              "src": "experience", "last": now}
    rate = sal * BASE_RATE * rate_mul / (1.0 + 0.25 * e["n"])
    if v < 0:
        rate *= 1.3
    e["w"] = round((1 - rate) * e["w"] + rate * v, 4)
    e["conf"] = round(min(1.0, e["conf"] + 0.15 * sal * rate_mul), 4)
    e["n"] += 1
    e["last"] = now
    e["src"] = "experience"
    tastes[topic] = e
    return e


# Score needed before a topic counts as an interest or dislike in summary().
# Lowered from 0.15: one experience alone (~0.02) never crossed it, so tastes
# only showed up after several experiences. Two or three now do.
INTEREST_THRESH = 0.08


def forbid(s, topic: Any, now: float) -> dict | None:
    """Fixed boundary from an explicit stop request ("ne legyen ilyen").
    Only call this on an explicit request; a single bad experience must go
    through record() instead."""
    return record(s, topic, -1.0, 1.0, now, explicit_stop=True)


def record(s, topic: Any, valence: float, salience: float, now: float,
           tags: Iterable[str] | None = None, explicit_stop: bool = False) -> dict | None:
    """Learn from one experience. Returns the updated entry, or None when the
    topic is empty or a hard rule forbids storing it.

    Nothing becomes fixed from a single or a few refusals: they only move the
    weight. A topic path like "horror/jumpscare" also nudges its parent
    ("horror") weakly, so a bad sub-type does not poison the whole genre.
    Only an explicit request to stop (explicit_stop=True, set by the operator
    or a clear stated "ne legyen ilyen") turns a topic into a fixed boundary."""
    topic = norm(topic)
    if not topic or not rules.taste_allowed(tags):
        return None
    tastes = _store(s)
    v = _clamp(float(valence), -1.0, 1.0)
    sal = _clamp(float(salience), 0.0, 1.0)
    e = _update(tastes, topic, v, sal, now)
    if "/" in topic:
        _update(tastes, topic.split("/", 1)[0], v, sal, now, rate_mul=0.3)
    if explicit_stop:
        e["kind"] = "boundary"
        e["w"] = min(e["w"], -0.8)
        e["conf"] = max(e["conf"], 0.8)
    return {**e, "topic": topic}


def _age_factor(e: dict, now: float) -> float:
    age_d = max(0.0, (now - e.get("last", now)) / DAY)
    return 0.5 ** (age_d / HALF_LIFE_D)


def score(s, topic: Any, now: float) -> float:
    """-1..1: how much she likes (+) or dislikes (-) this, confidence and age applied."""
    e = _store(s).get(norm(topic))
    if not e:
        return 0.0
    if e["kind"] == "boundary":
        return e["w"]
    return e["w"] * e["conf"] * _age_factor(e, now)


def is_boundary(s, topic: Any) -> bool:
    e = _store(s).get(norm(topic))
    return bool(e and e["kind"] == "boundary")


def bias(s, topic: Any, now: float) -> float:
    """Multiplier for how tempting a topic is. Boundaries give 0 (never picked)."""
    if is_boundary(s, topic):
        return 0.0
    return max(0.0, 1.0 + 0.6 * score(s, topic, now))


def pick(s, candidates: list[dict], now: float, novelty: float = 0.5,
         rng: random.Random | None = None) -> str | None:
    """Choose a topic from candidates ({"topic", "tags"}) by taste, with a bit of
    curiosity for things she hasn't tried. Boundaries and hard-rule content are
    never chosen."""
    rng = rng or random.Random()
    tastes = _store(s)
    weighted: list[tuple[float, str]] = []
    for c in candidates or []:
        topic = norm(c.get("topic"))
        if not topic or not rules.taste_allowed(c.get("tags")):
            continue
        if is_boundary(s, topic):
            continue
        n = tastes.get(topic, {}).get("n", 0)
        curiosity = 0.3 * _clamp(novelty, 0.0, 1.0) * math.exp(-n / 2.0)
        weighted.append((max(0.05, 0.5 + 0.5 * score(s, topic, now) + curiosity), topic))
    if not weighted:
        return None
    r = rng.random() * sum(w for w, _ in weighted)
    for w, topic in weighted:
        r -= w
        if r <= 0:
            return topic
    return weighted[-1][1]


def summary(s, now: float, k: int = 8) -> dict[str, list[dict]]:
    """What she has (so far) figured out about herself."""
    out: dict[str, list[dict]] = {"interests": [], "dislikes": [], "boundaries": []}
    for topic, e in _store(s).items():
        sc = score(s, topic, now)
        row = {"topic": topic, "score": round(sc, 3), "n": e["n"], "src": e["src"]}
        if e["kind"] == "boundary":
            out["boundaries"].append(row)
        elif sc >= INTEREST_THRESH:
            out["interests"].append(row)
        elif sc <= -INTEREST_THRESH:
            out["dislikes"].append(row)
    for key in out:
        out[key].sort(key=lambda r: -abs(r["score"]))
        out[key] = out[key][:k]
    return out
