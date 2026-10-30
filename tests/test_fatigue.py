import time
import unittest

from limbic import Limbic
from limbic.appraisal import Appraisal
from limbic.engine import circadian
from limbic.temperament import Temperament


def at(h, m=0, day=26):
    return time.mktime((2026, 10, day, h, m, 0, 0, 0, -1))


def mind(**temp):
    t0 = at(10)
    return Limbic("f", temperament={"name": "f", "quiet_hours": [], **temp}, clock=lambda: t0), t0


class Circadian(unittest.TestCase):
    def test_shape(self):
        T = Temperament()
        self.assertEqual(circadian(T, at(12)), 0.0)
        self.assertEqual(circadian(T, at(18)), 0.0)
        self.assertGreater(circadian(T, at(22, 30)), 0.2)          # winding down before bed
        self.assertGreater(circadian(T, at(3)), circadian(T, at(23, 30)))   # small hours are the worst
        self.assertGreater(circadian(T, at(2, 15)), 0.8)
        self.assertLess(circadian(T, at(7, 30)), 0.1)
        self.assertEqual(circadian(T, at(9)), 0.0)
        T.circadian_strength = 0
        self.assertEqual(circadian(T, at(3)), 0.0)

    def test_sleepiness_lowers_arousal_and_shows_in_prompt(self):
        m, t0 = mind()
        m.engine.advance(m.state, at(2, 30, 27))
        self.assertGreater(m.state.drives["sleepiness"], 0.7)
        self.assertLess(m.state.mood["a"], -0.1)
        self.assertIn("álmos", m.prompt(lang="hu", now=at(2, 31, 27)))


class Sources(unittest.TestCase):
    def test_pleasant_chat_tires_less_than_work(self):
        a, t0 = mind()
        b, _ = mind()
        for i in range(10):
            a.feel(Appraisal(event="chat", valence=0.3, intensity=0.2, agency="other", target="Ádám", affinity=0.5),
                   now=t0 + 60 * i, speaker="Ádám")
            b.feel(Appraisal(event="task", valence=0.1, intensity=0.2, effort=0.5, competence=0.3, agency="self",
                             interaction=False), now=t0 + 60 * i)
        self.assertLess(a.state.drives["fatigue"], 0.5 * b.state.drives["fatigue"])
        self.assertEqual(max(a.state.fatigue_src, key=a.state.fatigue_src.get), "social")
        self.assertEqual(max(b.state.fatigue_src, key=b.state.fatigue_src.get), "work")

    def test_prompt_names_the_source(self):
        m, t0 = mind()
        m.state.drives["fatigue"] = 0.7
        m.state.fatigue_src = {"social": 0.5, "work": 0.1}
        self.assertIn("beszélgetés", m.prompt(lang="hu", now=t0 + 1))
        m.state.fatigue_src = {"work": 0.5}
        self.assertIn("munkában", m.prompt(lang="hu", now=t0 + 2))

    def test_dull_things_tire_more(self):
        a, t0 = mind()
        b, _ = mind()
        for i in range(10):
            a.feel(Appraisal(event="x", valence=0.4, intensity=0.2, affinity=0.6, interaction=False), now=t0 + 60 * i)
            b.feel(Appraisal(event="x", valence=-0.2, intensity=0.2, affinity=-0.5, interaction=False), now=t0 + 60 * i)
        self.assertLess(a.state.drives["fatigue"], b.state.drives["fatigue"])


class TiredMood(unittest.TestCase):
    def test_tired_takes_bad_news_harder(self):
        fresh, t0 = mind(circadian_strength=0)
        tired, _ = mind(circadian_strength=0)
        tired.state.drives["fatigue"] = 0.9
        ap = dict(event="bad_news", valence=-0.5, intensity=0.4, relevance=0.7, interaction=False)
        r1 = fresh.feel(Appraisal(**ap), now=t0 + 1)
        r2 = tired.feel(Appraisal(**ap), now=t0 + 1)
        self.assertGreater(r2["effects"]["effective_intensity"], r1["effects"]["effective_intensity"])
        self.assertIn("frustration", r2["effects"]["emotions"])
        self.assertNotIn("frustration", r1["effects"]["emotions"])


class LeisureFatigue(unittest.TestCase):
    def test_gripping_story_lasts_longer_than_dull_one(self):
        def steps(valence):
            m, t0 = mind(circadian_strength=0)
            m.state.drives["novelty"] = 0.95
            m.state.last_interaction = t0 - 5 * 3600
            m.leisure_start("lector", now=t0)
            n = 0
            while n < 200:
                n += 1
                r = m.leisure_experience(appraisals=[{"event": "scene", "valence": valence, "intensity": 0.25,
                                                      "novelty": 0.5}], now=t0 + 60 * n)
                if not r["continue"]["continue"]:
                    break
            return n, m.state.drives["fatigue"], r["continue"]["reason"]
        good_n, good_f, good_r = steps(0.6)
        dull_n, dull_f, _ = steps(-0.1)
        self.assertGreaterEqual(good_n, 40)          # a good book holds her for a while (~an hour)
        self.assertNotEqual(good_r, "tired")
        self.assertLess(good_f / good_n, dull_f / dull_n)   # and tires her less per scene

    def test_mostly_sleeps_at_night_and_stops_when_sleepy(self):
        import random
        m, _ = mind()
        night = at(2, 30, 27)
        m.state.drives["novelty"] = 0.95
        m.state.last_interaction = night - 6 * 3600
        m.engine.advance(m.state, night)
        rng = random.Random(1)
        starts = sum(1 for i in range(300)
                     if m.leisure_decide(available=["web", "lector"], now=night, rng=rng)["start"])
        self.assertLessEqual(starts, 3)
        m.state.leisure["current"] = None
        m.leisure_start("web", now=night)
        for i in range(10):
            r = m.leisure_experience(appraisals=[{"event": "x", "valence": 0.15, "intensity": 0.2}], now=night + 60 * (i + 1))
            if not r["continue"]["continue"]:
                break
        self.assertEqual(r["continue"]["reason"], "sleepy")

    def test_no_fixed_tired_line_for_what_she_loves(self):
        def run(act, valence, taste):
            m, t0 = mind(circadian_strength=0)
            m.state.drives["fatigue"] = 0.85
            m.state.drives["novelty"] = 0.8
            m.state.leisure["taste"] = {act: {"enjoy": taste, "n": 5}}
            m.leisure_start(act, now=t0)
            n = 0
            while n < 150:
                n += 1
                r = m.leisure_experience(appraisals=[{"event": "x", "valence": valence, "intensity": 0.25}], now=t0 + 35 * n)
                if not r["continue"]["continue"]:
                    break
            return n, r["continue"]["reason"]
        loved, _ = run("lector", 0.6, 0.6)
        disliked, why = run("web", 0.1, -0.2)
        self.assertGreaterEqual(loved, 40)     # worn out, but one more chapter...
        self.assertLessEqual(disliked, 5)
        self.assertEqual(why, "tired")

    def test_rested_feels_good(self):
        m, t0 = mind(circadian_strength=0)
        m.state.drives["fatigue"] = 0.85
        m.leisure_decide(available=["web"], force=True, now=t0)
        m.state.leisure["current"] = None
        m.leisure_decide(available=["web"], force=True, now=t0 + 8 * 3600)
        self.assertTrue(any(e["cause"] == "kipihentem magam" for e in m.state.emotions.values()))


class Restorative(unittest.TestCase):
    def test_enjoyable_reading_eases_work_fatigue(self):
        m, t0 = mind(circadian_strength=0)
        m.state.drives["fatigue"] = 0.5
        m.state.fatigue_src = {"work": 0.5}
        m.state.drives["novelty"] = 0.95
        m.leisure_start("lector", now=t0)
        for i in range(20):
            m.leisure_experience(appraisals=[{"event": "scene", "valence": 0.6, "intensity": 0.25}], now=t0 + 60 * (i + 1))
        self.assertLess(m.state.drives["fatigue"], 0.5 * 0.95)   # better than time alone would do
        self.assertLess(m.state.fatigue_src.get("work", 0), 0.4)


class Quitting(unittest.TestCase):
    def test_puts_down_a_dull_story(self):
        m, t0 = mind(circadian_strength=0)
        m.state.drives["novelty"] = 0.95
        m.leisure_start("lector", now=t0)
        for i in range(30):
            r = m.leisure_experience(appraisals=[{"event": "scene", "valence": 0.0, "intensity": 0.2}],
                                     now=t0 + 35 * (i + 1))
            if not r["continue"]["continue"]:
                break
        self.assertEqual(r["continue"]["reason"], "it isn't grabbing me")
        self.assertLessEqual(i, 12)
