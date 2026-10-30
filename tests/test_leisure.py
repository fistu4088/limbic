import random
import unittest

from limbic import Limbic
from limbic.appraisal import Appraisal

H = 3600.0
T0 = 1_760_000_000.0


def mind(**temp):
    t = {"name": "teszt", "quiet_hours": [], **temp}
    m = Limbic("t", temperament=t, clock=lambda: T0)
    return m


def bore(m, now):
    """Make her properly bored: high novelty need, nobody around."""
    m.state.drives["novelty"] = 0.95
    m.state.last_interaction = now - 5 * H
    m.engine.advance(m.state, now)


def run_days(m, start, minutes, avail=("web", "lector"), seed=1):
    rng = random.Random(seed)
    started = []
    t = start
    for _ in range(minutes):
        t += 60
        d = m.leisure_decide(available=list(avail), now=t, rng=rng)
        if d["start"]:
            m.leisure_start(d["start"], resume=d.get("resume", False), now=t)
            started.append(d["start"])
            m.leisure_finish(rating=0.0, now=t + 60)
            m.state.drives["novelty"] = 0.95  # bored again
    return started, t


class LeisureTests(unittest.TestCase):
    def test_bored_neutral_agent_starts_something(self):
        m = mind()
        bore(m, T0 + 10 * H)
        started, _ = run_days(m, T0 + 10 * H, 120)
        self.assertTrue(started, "a bored, neutral agent should pick something up within two hours")

    def test_not_bored_does_nothing(self):
        m = mind()
        rng = random.Random(3)
        for i in range(120):
            m.state.last_interaction = T0 + 60 * i - 1800   # talked half an hour ago, nothing missing
            d = m.leisure_decide(available=["web"], now=T0 + 60 * i, rng=rng)
            self.assertIsNone(d["start"])
        self.assertEqual(d["reason"], "not bored")

    def test_depressed_rarely_does_anything(self):
        happy, sad = (mind(leisure_max_per_day=999, circadian_strength=0),
                      mind(leisure_max_per_day=999, circadian_strength=0))
        bore(happy, T0 + H)
        bore(sad, T0 + H)
        sad.state.mood["p"] = sad.state.baseline["p"] = -0.6
        sad.engine.t.baseline_min = -0.8
        a, _ = run_days(happy, T0 + H, 600, seed=7)
        b, _ = run_days(sad, T0 + H, 600, seed=7)
        self.assertGreater(len(a), 3 * max(1, len(b)), (len(a), len(b)))

    def test_in_conversation_waits(self):
        m = mind()
        bore(m, T0 + H)
        m.state.last_interaction = T0 + H - 120
        d = m.leisure_decide(available=["web"], now=T0 + H, rng=random.Random(1))
        self.assertEqual(d["reason"], "in a conversation")

    def test_tired_prefers_what_she_loves(self):
        def picks(fatigue):
            m = mind(circadian_strength=0)
            bore(m, T0 + H)
            m.state.leisure = {"current": None, "taste": {"lector": {"enjoy": 0.6, "n": 5}, "web": {"enjoy": 0.0, "n": 5}},
                               "resting": False, "log": [], "seq": 0, "last_end": 0.0, "available": {}}
            m.state.drives["fatigue"] = fatigue
            rng = random.Random(3)
            p = [m.leisure_decide(available=["web", "lector"], force=True, now=T0 + H, rng=rng)["start"] for _ in range(300)]
            return p.count("lector") / max(1, p.count("web"))
        self.assertGreater(picks(0.85), 2 * picks(0.1))   # tired: the loved thing wins even more clearly

    def test_steps_tire_and_stop(self):
        m = mind()
        bore(m, T0 + H)
        m.leisure_start("web", now=T0 + H)
        cont = True
        steps = 0
        while cont and steps < 300:
            steps += 1
            r = m.leisure_experience(appraisals=[{"event": "interesting_fact", "valence": 0.3, "intensity": 0.2,
                                                  "novelty": 0.6}], now=T0 + H + 30 * steps)
            cont = r["continue"]["continue"]
        self.assertLess(steps, 300)
        self.assertIn(r["continue"]["reason"], ("tired", "time is up", "not bored any more, and it's not that great"))

    def test_reading_moves_the_mood(self):
        m = mind()
        bore(m, T0 + H)
        m.leisure_start("web", now=T0 + H)
        p0 = m.state.mood["p"]
        m.leisure_experience(appraisals=[{"event": "bad_news", "valence": -0.7, "intensity": 0.5,
                                          "relevance": 0.8, "summary": "szomorú hír"}], now=T0 + H + 60)
        self.assertLess(m.state.mood["p"], p0)
        self.assertTrue(any(e["name"] == "sadness" for e in m.state.emotions.values()))
        # it is not social contact
        self.assertEqual(m.state.last_interaction, T0 + H - 5 * H)

    def test_conversation_pauses_and_resume(self):
        m = mind()
        bore(m, T0 + H)
        m.leisure_start("lector", topic="Solo Leveling", now=T0 + H)
        m.leisure_experience(appraisals=[{"event": "triumph", "valence": 0.5, "intensity": 0.3}],
                             bookmark={"slug": "solo-leveling", "scene": 3}, now=T0 + H + 60)
        m.feel(Appraisal(event="chat", valence=0.1, intensity=0.1, agency="other", target="Ádám"),
               now=T0 + H + 120, speaker="Ádám")
        cur = m.state.leisure["current"]
        self.assertEqual(cur["status"], "paused")
        block = m.prompt(lang="hu", interlocutor="Ádám", now=T0 + H + 121)
        self.assertIn("félretetted", block)
        self.assertIn("Solo Leveling", block)
        block2 = m.prompt(lang="hu", interlocutor="Ádám", now=T0 + H + 180)
        self.assertNotIn("félretetted", block2)       # announced once
        self.assertIn("Félretett", block2)
        # while chatting nothing new starts
        d = m.leisure_decide(available=["web", "lector"], force=False, now=T0 + H + 200, rng=random.Random(1))
        self.assertEqual(d["reason"], "in a conversation")
        # "folytasd": resumes even though the chat was just now
        m.leisure_resume(now=T0 + H + 240)
        d = m.leisure_decide(available=["web", "lector"], now=T0 + H + 260, rng=random.Random(1))
        self.assertEqual((d["start"], d["resume"]), ("lector", True))
        cur = m.leisure_start("lector", resume=True, now=T0 + H + 260)
        self.assertEqual(cur["bookmark"], {"slug": "solo-leveling", "scene": 3})
        self.assertEqual(cur["steps"], 1)

    def test_taste_shapes_choices(self):
        m = mind()
        bore(m, T0 + H)
        t = T0 + H
        for i in range(4):   # web was great, the story was a drag
            t += 4 * H
            m.leisure_start("web", now=t)
            for k in range(3):
                m.leisure_experience(appraisals=[{"event": "discovery", "valence": 0.7, "intensity": 0.35}],
                                     now=t + 60 * (k + 1))
            m.leisure_finish(rating=0.9, now=t + 300)
            t += 4 * H
            m.leisure_start("lector", now=t)
            for k in range(3):
                m.leisure_experience(appraisals=[{"event": "dull", "valence": -0.4, "intensity": 0.2}],
                                     now=t + 60 * (k + 1))
            m.leisure_finish(rating=-0.6, now=t + 300)
        taste = m.state.leisure["taste"]
        self.assertGreater(taste["web"]["enjoy"], 0.3)
        self.assertLess(taste["lector"]["enjoy"], 0)
        m.state.leisure["log"] = []   # forget satiety, compare taste only
        bore(m, t + 10 * H)
        rng = random.Random(11)
        picks = [m.leisure_decide(available=["web", "lector"], force=True, now=t + 10 * H, rng=rng)["start"]
                 for _ in range(300)]
        self.assertGreater(picks.count("web"), 3 * max(1, picks.count("lector")), picks.count("lector"))

    def test_satiety_varies_choices(self):
        m = mind(leisure_gap_min=0, leisure_max_per_day=999)
        bore(m, T0 + H)
        picks, _ = run_days(m, T0 + H, 2000, seed=5)
        self.assertGreater(picks.count("lector"), 0)
        self.assertGreater(picks.count("web"), 0)

    def test_finish_feeling_and_log(self):
        m = mind()
        bore(m, T0 + H)
        m.leisure_start("web", topic="rpg", now=T0 + H)
        m.leisure_experience(appraisals=[{"event": "funny", "valence": 0.6, "intensity": 0.4, "humor": 0.5}],
                             now=T0 + H + 60)
        e = m.leisure_finish(rating=0.8, summary="vicces cikk", now=T0 + H + 120)
        self.assertGreater(e["enjoy"], 0.25)
        self.assertIsNone(m.state.leisure["current"])
        snap = m.snapshot()
        self.assertEqual(snap["leisure"]["log"][0]["topic"], "rpg")
        self.assertIn("élvezted", m.prompt(lang="hu", now=T0 + H + 180))

    def test_keyword_reading_appraisal_is_not_social(self):
        m = mind()
        bore(m, T0 + H)
        m.leisure_start("web", now=T0 + H)
        r = m.leisure_experience(text="Szuper új felfedezés: működik az új akkumulátor!", kind="web",
                                 title="Tech hír", now=T0 + H + 60)
        for res in r["results"]:
            self.assertFalse(res["appraisal"]["interaction"])
            self.assertIsNone(res["appraisal"]["target"])
        self.assertEqual(m.state.last_interaction, T0 + H - 5 * H)


if __name__ == "__main__":
    unittest.main()


class JournalTests(unittest.TestCase):
    def test_journal_eases_negative_feelings(self):
        m = mind()
        bore(m, T0 + H)
        m.feel(Appraisal(event="bad_news", valence=-0.7, intensity=0.6, relevance=0.8, interaction=False),
               now=T0 + H)
        before = m.engine.emotion_level(m.state, "sadness")
        m.leisure_start("journal", now=T0 + H + 1)
        m.leisure_experience(appraisals=[{"event": "journaling", "valence": 0.1, "intensity": 0.1}],
                             now=T0 + H + 2)
        after = m.engine.emotion_level(m.state, "sadness")
        self.assertLess(after, before * 0.9)


class RestlessTests(unittest.TestCase):
    def test_empty_hours_make_restless(self):
        from limbic import leisure
        m = mind()
        m.state.drives["novelty"] = 0.05
        m.state.last_interaction = T0
        m.engine.advance(m.state, T0 + 600)
        self.assertLess(leisure.urge(m.engine, m.state), m.engine.t.leisure_urge_min)
        m.engine.advance(m.state, T0 + 4 * H)
        self.assertGreater(leisure.urge(m.engine, m.state), m.engine.t.leisure_urge_min)
