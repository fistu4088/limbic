"""High-level facade: one emotional agent backed by a Store.

    from limbic import Limbic
    mind = Limbic("aria", store=Store("limbic.db"), temperament={...})
    block = mind.prompt(lang="hu", interlocutor="Ádám")   # before the LLM call
    mind.observe(user_text, speaker="Ádám")                 # after the turn
"""
from __future__ import annotations

import inspect
import time
from typing import Any, Callable

from . import intents as intents_mod
from . import leisure, needs, thread
from .appraisal import Appraisal, KeywordAppraiser
from .engine import Engine, State
from .store import Store
from .temperament import Temperament


def _compact(snap: dict) -> dict:
    return {"label": snap["label"], "mood": snap["mood"], "baseline": snap["baseline"],
            "drives": snap["drives"],
            "emotions": [{"name": e["name"], "target": e["target"], "i": e["i"]} for e in snap["emotions"][:8]]}


REL_LEVELS = {"affection": (0.0, 1.0), "trust": (-1.0, 1.0), "resentment": (0.0, 1.0), "fear": (0.0, 1.0),
              "longing": (0.0, 1.0), "intimacy": (0.0, 1.0), "attraction": (0.0, 1.0)}


class Limbic:
    SNAPSHOT_EVERY_S = 300.0

    def __init__(self, agent_id: str, store: Store | None = None,
                 temperament: Temperament | dict | None = None,
                 appraiser: Any = None, clock: Callable[[], float] = time.time) -> None:
        self.id = agent_id
        self.store = store or Store(":memory:")
        self.clock = clock
        row = self.store.get_agent(agent_id)
        if row is not None and temperament is None:
            temp = Temperament.from_dict(row["temperament"])
        elif isinstance(temperament, Temperament):
            temp = temperament
        else:
            temp = Temperament.from_dict(temperament or (row or {}).get("temperament"))
        self.engine = Engine(temp)
        now = self.clock()
        self.state = State.from_dict(row["state"]) if row else self.engine.new_state(now)
        self.appraiser = appraiser or KeywordAppraiser(temp)
        if row is None or temperament is not None:
            self.save(force_snapshot=True)

    @property
    def temperament(self) -> Temperament:
        return self.engine.t

    def set_temperament(self, data: dict) -> None:
        merged = {**self.engine.t.to_dict(), **(data or {})}
        self.engine.t = Temperament.from_dict(merged)
        if hasattr(self.appraiser, "temperament"):
            self.appraiser.temperament = self.engine.t
        self.save()

    # ---- persistence -------------------------------------------------
    def save(self, force_snapshot: bool = False) -> None:
        now = self.state.t
        self.store.save_agent(self.id, self.engine.t.to_dict(), self.state.to_dict(), now)
        if force_snapshot or now - self.store.last_snapshot_t(self.id) >= self.SNAPSHOT_EVERY_S:
            self.store.add_snapshot(self.id, now, _compact(self.engine.snapshot(self.state)))

    # ---- inputs ------------------------------------------------------
    def tick(self, now: float | None = None) -> dict:
        self.engine.advance(self.state, now if now is not None else self.clock())
        self.save()
        return self.snapshot()

    def feel(self, appraisal: Appraisal | dict, now: float | None = None,
             source: str = "manual", speaker: str | None = None, private: bool = False) -> dict:
        ap = appraisal if isinstance(appraisal, Appraisal) else Appraisal.from_dict(appraisal)
        t = now if now is not None else self.clock()
        effects = self.engine.apply(self.state, ap, t, speaker=speaker, private=private)
        eid = self.store.add_event(self.id, t, ap.event, source, speaker or ap.target,
                                   ap.to_dict(), effects)
        self.save(force_snapshot=True)
        return {"id": eid, "appraisal": ap.to_dict(), "effects": effects}

    def observe(self, text: str, speaker: str | None = None, context: str | None = None,
                now: float | None = None, private: bool = False, channel: str | None = None) -> list[dict]:
        """Appraise a message (via the configured appraiser) and feel it."""
        if speaker and channel:
            self.note_channel(speaker, channel, now)
        kwargs = {}
        try:
            if "relationships" in inspect.signature(self.appraiser.appraise).parameters:
                kwargs["relationships"] = self.state.relationships
        except (TypeError, ValueError):
            pass
        aps = self.appraiser.appraise(text, speaker, context, **kwargs)
        t = now if now is not None else self.clock()
        return [self.feel(ap, now=t, source="observe", speaker=speaker, private=private) for ap in aps]

    def read(self, text: str, title: str | None = None, memory: str | None = None,
             now: float | None = None) -> list[dict]:
        """Feel a scene of a story the agent is reading (manga, novel...).
        Fiction moves mood and leaves episodic memories, but never counts as
        social contact and never touches real relationships or desire."""
        fn = getattr(self.appraiser, "appraise_story", None)
        if fn is None:
            from .appraisal import KeywordAppraiser
            fn = KeywordAppraiser(self.engine.t).appraise_story
        aps = fn(text, title=title, memory=memory, relationships=self.state.relationships)
        t = now if now is not None else self.clock()
        return [self.feel(ap, now=t, source="story") for ap in aps]

    def see(self, text: str, question: str | None = None, speaker: str | None = None,
            private: bool = False, now: float | None = None, at: float | None = None,
            past_weight: float = 0.3) -> list[dict]:
        """Feel an image she looked at (from a vision model's description).
        `at` = when she saw it, for memories added afterwards: the event is stored at
        that time, but it is applied now and only at `past_weight` strength (it is
        remembered, not lived again)."""
        fn = getattr(self.appraiser, "appraise_image", None)
        if fn is None:
            from .appraisal import KeywordAppraiser
            fn = KeywordAppraiser(self.engine.t).appraise_image
        aps = fn(text, question=question, shown_by=speaker)
        t = now if now is not None else self.clock()
        past = at is not None and at < t - 60
        out = []
        for ap in aps:
            if past:
                ap.intensity *= past_weight
                ap.summary = (ap.summary or "láttam egy képet") + " (korábbi emlék)"
            effects = self.engine.apply(self.state, ap, t, speaker=None, private=private)
            needs.image_arousal(self.engine, self.state, ap, speaker, private, t,
                                (t - at) if past else None, past_weight, effects)
            eid = self.store.add_event(self.id, at if past else t, ap.event, "image", speaker,
                                       ap.to_dict(), effects)
            out.append({"id": eid, "appraisal": ap.to_dict(), "effects": effects})
        self.save(force_snapshot=True)
        return out

    # ---- leisure (free time) -------------------------------------------
    def leisure_decide(self, available: list[str] | None = None, force: bool = False,
                       now: float | None = None, rng=None) -> dict:
        t = now if now is not None else self.clock()
        self.engine.advance(self.state, t)
        res = leisure.decide(self.engine, self.state, t, available=available, rng=rng, force=force)
        self.save()
        return res

    def leisure_start(self, activity: str, resume: bool = False, topic: str | None = None,
                      now: float | None = None) -> dict:
        if activity not in leisure.ACTIVITIES:
            raise ValueError(f"unknown activity {activity}")
        t = now if now is not None else self.clock()
        self.engine.advance(self.state, t)
        cur = leisure.start(self.engine, self.state, activity, t, resume=resume, topic=topic)
        self.store.add_event(self.id, t, "leisure_start", "leisure", None,
                             {"event": "leisure_start", "summary": f"{'folytatom' if resume else 'belekezdek'}: "
                              f"{leisure.label(activity)}" + (f" ({topic})" if topic else "")},
                             {"activity": activity, "resume": resume, "id": cur["id"]})
        self.save(force_snapshot=True)
        return cur

    def leisure_experience(self, text: str | None = None, kind: str = "web", title: str | None = None,
                           interests: str | None = None, appraisals: list | None = None,
                           bookmark=None, note: str | None = None, now: float | None = None) -> dict:
        """One step of the current activity. Either `text` (appraised here as
        web reading / story) or ready `appraisals` (e.g. a script run)."""
        t = now if now is not None else self.clock()
        self.engine.advance(self.state, t)
        cur = (self.state.leisure or {}).get("current")
        if not cur or cur["status"] != "running":
            raise ValueError("no running activity")
        if appraisals is not None:
            aps = [a if isinstance(a, Appraisal) else Appraisal.from_dict(a) for a in appraisals]
            for a in aps:
                a.interaction = False
        elif text:
            if kind == "story":
                fn = getattr(self.appraiser, "appraise_story", None) or KeywordAppraiser(self.engine.t).appraise_story
                aps = fn(text, title=title, memory=interests, relationships=self.state.relationships)
            else:
                fn = getattr(self.appraiser, "appraise_reading", None) or KeywordAppraiser(self.engine.t).appraise_reading
                aps = fn(text, title=title, interests=interests, relationships=self.state.relationships)
        else:
            aps = []
        res = [self.feel(a, now=t, source=f"leisure:{cur['activity']}") for a in aps]
        cur = leisure.experience(self.engine, self.state, [r["effects"] for r in res], aps, t,
                                 bookmark=bookmark, note=note)
        self.save(force_snapshot=True)
        return {"results": res, "current": cur,
                "continue": leisure.should_continue(self.engine, self.state, t)}

    def leisure_should_continue(self, now: float | None = None) -> dict:
        t = now if now is not None else self.clock()
        self.engine.advance(self.state, t)
        return leisure.should_continue(self.engine, self.state, t)

    def leisure_finish(self, rating: float | None = None, summary: str | None = None,
                       reason: str | None = None, now: float | None = None) -> dict:
        t = now if now is not None else self.clock()
        self.engine.advance(self.state, t)
        entry = leisure.finish(self.engine, self.state, t, rating=rating, summary=summary, reason=reason)
        # the overall verdict is itself a small experience: "that was nice" / "waste of time"
        e = entry["enjoy"]
        word = "jó volt" if e > 0.25 else "elment" if e > -0.1 else "nem élveztem"
        ap = Appraisal(event="leisure_done", valence=0.6 * e, intensity=min(0.35, 0.1 + 0.3 * abs(e)),
                       relevance=0.4, agency="self", interaction=False, novelty=0.1,
                       summary=f"{leisure.label(entry['activity'])}" +
                               (f" ({entry['topic']})" if entry.get("topic") else "") + f": {word}")
        if abs(e) > 0.1:
            self.feel(ap, now=t, source="leisure")
        self.store.add_event(self.id, t, "leisure_end", "leisure", None,
                             {"event": "leisure_end", "summary": ap.summary, "valence": ap.valence}, entry)
        self.save(force_snapshot=True)
        return entry

    def leisure_resume(self, now: float | None = None) -> dict:
        t = now if now is not None else self.clock()
        cur = leisure.request_resume(self.state, t)
        self.save()
        return cur

    def leisure_pause(self, by: str | None = None, now: float | None = None) -> dict | None:
        cur = leisure.pause(self.state, now if now is not None else self.clock(), by)
        self.save()
        return cur

    def consolidate(self, now: float | None = None, window_s: float | None = None) -> dict:
        t = now if now is not None else self.clock()
        since = t - window_s if window_s else self.state.last_consolidation
        evs = self.store.events(self.id, since=since, until=t, limit=2000)
        evs = [e for e in evs if e["kind"] not in ("consolidation", "recall")]
        result = self.engine.consolidate(self.state, evs, t)
        self.store.add_event(self.id, t, "consolidation", "system", None, None, result)
        self.save(force_snapshot=True)
        return result

    def recall(self, event_id: int, now: float | None = None) -> dict:
        ev = self.store.get_event(self.id, event_id)
        if ev is None:
            raise KeyError(event_id)
        t = now if now is not None else self.clock()
        result = self.engine.recall(self.state, ev, t, times_recalled=ev["recalled"])
        self.store.mark_recalled(event_id)
        self.store.add_event(self.id, t, "recall", "system", None, {"event": ev["id"], "summary": (ev["appraisal"] or {}).get("summary")}, result)
        self.save(force_snapshot=True)
        return result

    def reset(self, now: float | None = None) -> dict:
        self.state = self.engine.new_state(now if now is not None else self.clock())
        self.store.add_event(self.id, self.state.t, "reset", "system", None, None, None)
        self.save(force_snapshot=True)
        return self.snapshot()

    # ---- autonomy ----------------------------------------------------
    def evaluate_intents(self, now: float | None = None) -> list[dict]:
        """Advance time, expire old intents, create new ones. Call periodically."""
        t = now if now is not None else self.clock()
        self.engine.advance(self.state, t)
        new = intents_mod.evaluate(self.engine, self.state, t)
        for it in new:
            self.store.add_event(self.id, t, "intent", "autonomy", it["target"], None, it)
        self.save(force_snapshot=bool(new))
        return new

    def intents(self, status: str | None = None) -> list[dict]:
        return [i for i in self.state.intents if status is None or i["status"] == status]

    def claim(self, intent_id: int, now: float | None = None) -> dict:
        t = now if now is not None else self.clock()
        self.engine.advance(self.state, t)
        it = intents_mod.claim(self.engine, self.state, intent_id, t)
        self.store.add_event(self.id, t, "outreach", "autonomy", it["target"], None, it)
        self.save(force_snapshot=True)
        return it

    def dismiss(self, intent_id: int, now: float | None = None) -> dict:
        it = intents_mod.dismiss(self.state, intent_id, now if now is not None else self.clock())
        self.save()
        return it

    def instruction(self, intent_id: int, lang: str = "en") -> str:
        it = next(i for i in self.state.intents if i["id"] == intent_id)
        return intents_mod.render_instruction(self.engine, self.state, it, lang)

    def note_reply(self, now: float | None = None) -> None:
        """Tell limbic the agent answered (keeps check-in logic honest)."""
        thread.on_agent_reply(self.state, now if now is not None else self.clock())
        self.save()

    def note_channel(self, name: str, channel: str, now: float | None = None) -> None:
        t = now if now is not None else self.clock()
        r = self.state.relationships.setdefault(name, needs.new_relationship(t))
        needs.normalize_relationship(r, t)
        r["last_channel"] = str(channel)[:40]
        r["last_channel_t"] = t

    def set_relationship(self, name: str, **fields) -> dict:
        """Operator-only settings: consent, reachable, contact."""
        t = self.state.t
        r = self.state.relationships.setdefault(name, needs.new_relationship(t))
        needs.normalize_relationship(r, t)
        if "consent" in fields and isinstance(fields["consent"], dict):
            for k in ("romance", "adult"):
                if k in fields["consent"]:
                    r["consent"][k] = bool(fields["consent"][k])
        if "reachable" in fields:
            r["reachable"] = bool(fields["reachable"])
        if "contact" in fields:
            r["contact"] = str(fields["contact"] or "")[:200]
        # operator-set levels (dashboard): clamped, and logged so it stays traceable
        changed = {}
        for k, (lo, hi) in REL_LEVELS.items():
            if k in fields and fields[k] is not None:
                v = max(lo, min(hi, float(fields[k])))
                if abs(v - float(r.get(k, 0.0))) > 1e-9:
                    changed[k] = {"from": round(float(r.get(k, 0.0)), 4), "to": round(v, 4)}
                    r[k] = v
        if changed:
            if "longing" in changed and r["longing"] < 0.25:
                key = f"longing@{name}"
                if key in self.state.emotions:
                    self.state.emotions[key]["sustained"] = False
                    self.state.emotions[key]["i"] = min(self.state.emotions[key]["i"], r["longing"])
            self.store.add_event(self.id, self.clock(), "operator_edit", "operator", name,
                                 {"event": "operator_edit", "summary": f"operator set {', '.join(changed)} for {name}"},
                                 {"changed": changed})
        self.save(force_snapshot=bool(changed))
        return r

    def set_emotion(self, name: str, target: str | None = None, intensity: float = 0.0) -> dict:
        """Operator-only: set (or clear with 0) one feeling. It then fades like any other
        feeling of its kind. Adult feelings only when enabled. Logged as operator_edit."""
        from .emotions import EMOTIONS
        if name not in EMOTIONS:
            raise ValueError(f"unknown emotion {name}")
        if name in ("desire", "temptation") and not self.engine.t.adult_enabled:
            raise ValueError("adult feelings are disabled in the temperament")
        tgt = (target or None) if EMOTIONS[name].targeted else None
        key = f"{name}@{tgt}" if tgt else name
        v = max(0.0, min(1.0, float(intensity)))
        old = float((self.state.emotions.get(key) or {}).get("i", 0.0))
        if v <= 0.0:
            self.state.emotions.pop(key, None)
        else:
            e = self.state.emotions.setdefault(key, {"name": name, "target": tgt, "i": 0.0})
            e.update(i=v, cause="set by the operator", sustained=False)
        self.store.add_event(self.id, self.clock(), "operator_edit", "operator", tgt,
                             {"event": "operator_edit", "summary": f"operator set {key}"},
                             {"changed": {key: {"from": round(old, 4), "to": round(v, 4)}}})
        self.save(force_snapshot=True)
        return {"key": key, "i": v}

    def set_drives(self, **vals: float) -> dict:
        """Operator-only: set needs (0..1). They keep growing/being satisfied as usual."""
        d = self.state.drives
        changed = {}
        for k, v in vals.items():
            if k in d and v is not None:
                nv = max(0.0, min(1.0, float(v)))
                changed[k] = {"from": round(d[k], 4), "to": round(nv, 4)}
                d[k] = nv
        if changed:
            self.store.add_event(self.id, self.clock(), "operator_edit", "operator", None,
                                 {"event": "operator_edit", "summary": "operator set needs"}, {"changed": changed})
            self.save(force_snapshot=True)
        return dict(d)

    def set_mood(self, **axes: float) -> dict:
        """Operator-only: nudge mood (p/a/d, -1..1) and optionally the background mood
        (baseline_p/a/d). Mood drifts back towards baseline as usual."""
        changed = {}
        for k, v in axes.items():
            store, ax = (self.state.baseline, k[9:]) if k.startswith("baseline_") else (self.state.mood, k)
            if ax not in store or v is None:
                continue
            nv = max(-1.0, min(1.0, float(v)))
            if k.startswith("baseline_"):
                nv = max(self.engine.t.baseline_min, min(self.engine.t.baseline_max, nv))
            changed[k] = {"from": round(store[ax], 4), "to": round(nv, 4)}
            store[ax] = nv
        if changed:
            self.store.add_event(self.id, self.clock(), "operator_edit", "operator", None,
                                 {"event": "operator_edit", "summary": "operator set mood"}, {"changed": changed})
            self.save(force_snapshot=True)
        return {"mood": dict(self.state.mood), "baseline": dict(self.state.baseline)}

    def set_sexuality(self, openness: float) -> dict:
        """Operator-only: set the learned openness to intimacy (logged)."""
        sx = self.state.sexuality
        old = float(sx.get("openness", 0.0))
        sx["openness"] = max(0.0, min(1.0, float(openness)))
        self.store.add_event(self.id, self.clock(), "operator_edit", "operator", None,
                             {"event": "operator_edit", "summary": "operator set openness"},
                             {"changed": {"openness": {"from": round(old, 4), "to": round(sx["openness"], 4)}}})
        self.save(force_snapshot=True)
        return dict(sx)

    # ---- outputs -----------------------------------------------------
    def snapshot(self, interlocutor: str | None = None) -> dict:
        return self.engine.snapshot(self.state, interlocutor)

    def prompt(self, lang: str = "en", interlocutor: str | None = None, now: float | None = None) -> str:
        self.engine.advance(self.state, now if now is not None else self.clock())
        block = self.engine.prompt_block(self.state, lang=lang, interlocutor=interlocutor)
        cur = (self.state.leisure or {}).get("current")
        if interlocutor and cur and cur["status"] == "paused":
            cur["announced"] = True   # say "I was doing X" once, not every turn
        return block

    def modulators(self, interlocutor: str | None = None) -> dict:
        return self.engine.modulators(self.state, interlocutor)
