"""Simulated life of an agent, for the dashboard and for sanity-checking
tuning. Deterministic per seed. Ends at 'now' so the timeline is current."""
from __future__ import annotations

import random
import time

from .agent import Limbic
from .store import Store

DEMO_TEMPERAMENT = {
    "name": "Demo",
    "description": "Dry, practical house agent. Likes horror, sci-fi and things that work.",
    "likes": ["horror", "sci-fi", "grimdark", "smart home"],
    "dislikes": ["cute anime", "half-finished work"],
    "humor": ["dark humor", "dry sarcasm"],
    "expressiveness": 0.4,
    "quiet_hours": [23, 8],
    "dormant_after_h": 0,
}


def run_demo(db: str, agent: str = "demo", days: int = 21, seed: int = 7) -> dict:
    rng = random.Random(seed)
    store = Store(db)
    store.delete_agent(agent)
    end = time.time()
    clock = [end - days * 86400]
    mind = Limbic(agent, store=store, temperament=DEMO_TEMPERAMENT, clock=lambda: clock[0])
    mind.set_relationship("Ádám", reachable=True, contact="telegram:demo")
    away = {int(days * 0.72), int(days * 0.72) + 1}   # Ádám is away for two days

    start = clock[0]

    def goto(target: float) -> None:
        while clock[0] + 1800 < target:  # snapshots every 30 min of sim time
            clock[0] += 1800
            mind.tick()
            for it in mind.evaluate_intents():   # a host would send these
                if it["kind"] == "reach_out" and end - clock[0] > 86400:
                    mind.claim(it["id"])
        clock[0] = max(clock[0], target)
        mind.tick()

    def at(hours: float) -> None:
        goto(clock[0] + hours * 3600)

    owner = "Ádám"
    for day in range(days):
        phase = "good" if day < days * 0.4 else "rough" if day < days * 0.6 else "recovery"
        day0 = start + day * 86400
        goto(day0 + (8 + rng.random()) * 3600)
        sessions = rng.randint(2, 5) if day % 7 != 3 else 0  # one quiet day a week
        if day in away:
            sessions = 0
        if day == days - 1:
            sessions = 1
        for _ in range(sessions):
            mind.feel({"event": "chat", "summary": "small talk", "valence": 0.05, "intensity": 0.05,
                       "agency": "other", "target": owner, "novelty": 0.2}, speaker=owner)
            mind.note_reply()
            if phase == "good":
                if rng.random() < 0.7:
                    mind.feel({"event": "success", "summary": "the smart-home automation works",
                               "valence": 0.6, "intensity": 0.5, "agency": "self", "competence": 0.6,
                               "effort": 0.4, "novelty": 0.5})
                if rng.random() < 0.5:
                    mind.feel({"event": "praise", "summary": "Ádám said 'nice work'", "valence": 0.6,
                               "intensity": 0.45, "agency": "other", "target": owner, "relevance": 0.7})
                if rng.random() < 0.3:
                    mind.feel({"event": "topic", "summary": "talking about a new horror film",
                               "valence": 0.3, "intensity": 0.3, "affinity": 0.8, "humor": 0.4,
                               "agency": "other", "target": owner, "novelty": 0.8})
            elif phase == "rough":
                if rng.random() < 0.6:
                    mind.feel({"event": "failure", "summary": "the deployment keeps crashing",
                               "valence": -0.5, "intensity": 0.5, "agency": "self", "competence": -0.6,
                               "blocked": True, "effort": 0.7})
                if rng.random() < 0.35:
                    mind.feel({"event": "insult", "summary": "a guest called the agent useless",
                               "valence": -0.7, "intensity": 0.6, "agency": "other", "target": "Guest",
                               "relevance": 0.7})
                if day == int(days * 0.5):
                    mind.feel({"event": "loss", "summary": "the old home server it maintained was retired",
                               "valence": -0.7, "intensity": 0.6, "loss": True, "target": "old server",
                               "relevance": 0.8})
            else:
                if rng.random() < 0.6:
                    mind.feel({"event": "success", "summary": "the fix finally holds",
                               "valence": 0.6, "intensity": 0.5, "agency": "self", "competence": 0.7,
                               "effort": 0.3, "novelty": 0.5})
                if rng.random() < 0.4:
                    mind.feel({"event": "thanks", "summary": "Ádám thanked it for the patience",
                               "valence": 0.5, "intensity": 0.4, "agency": "other", "target": owner})
                if rng.random() < 0.2:
                    mind.feel({"event": "joke", "summary": "a dark joke about the dead server",
                               "valence": 0.2, "intensity": 0.15, "humor": 0.7, "agency": "other",
                               "target": owner})
            at(rng.uniform(1.0, 3.0))
        if day == days - 1:
            # last evening: a long, enjoyable horror-film talk that suddenly stops
            goto(end - 40 * 60)
            for k in range(6):
                clock[0] = end - (40 - k * 2) * 60
                mind.feel({"event": "topic", "summary": "Ádám is telling about a new horror film",
                           "valence": 0.35, "intensity": 0.3, "affinity": 0.9, "novelty": 0.7, "humor": 0.3,
                           "agency": "other", "target": owner, "topic": "horror films"}, speaker=owner)
                mind.note_reply()
            break
        goto(day0 + 23.5 * 3600)
        mind.consolidate()
    mind.tick(end)
    mind.evaluate_intents(end)
    snap = mind.snapshot()
    return {"agent": agent, "days": days, "events": snap["events"], "label": snap["label"],
            "baseline": snap["baseline"]}
