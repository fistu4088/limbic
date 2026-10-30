import unittest

from limbic import Limbic
from limbic.appraisal import Appraisal, LLMAppraiser


def mind(appraiser=None):
    clock = [1_800_000_000.0]
    m = Limbic("reader", clock=lambda: clock[0], appraiser=appraiser)
    return m, clock


def beat(**kw):
    d = {"event": "character_in_danger", "summary": "Tale: Mira is cornered by the wolf",
         "valence": -0.6, "intensity": 0.5, "relevance": 0.8, "agency": "other",
         "target": "Grey Wolf", "fiction": True}
    d.update(kw)
    return d


class FakeStoryLLM:
    """Stands in for an OpenAI-compatible endpoint."""
    def __init__(self, items):
        self.items = items
        self.seen = None

    def __call__(self, url, payload, headers):
        import json
        self.seen = payload
        return {"choices": [{"message": {"content": json.dumps({"appraisals": self.items})}}]}


class FictionFeelings(unittest.TestCase):
    def test_story_moves_mood_and_leaves_a_memory(self):
        m, clock = mind()
        before = m.snapshot()["mood"]["p"]
        r = m.feel(beat(), source="story")
        self.assertTrue(r["effects"]["emotions"])
        self.assertLess(m.snapshot()["mood"]["p"], before)
        evs = m.store.events(m.id, since=0)
        self.assertEqual(evs[-1]["source"], "story")

    def test_story_is_not_social_contact(self):
        m, clock = mind()
        last = m.state.last_interaction
        social = m.state.drives["social"]
        clock[0] += 3600
        m.feel(beat(valence=0.6, event="triumph", agency="circumstance", target="Mira"), source="story")
        self.assertEqual(m.state.last_interaction, last)
        self.assertGreaterEqual(m.state.drives["social"], social)

    def test_characters_are_never_people_to_miss(self):
        m, clock = mind()
        for _ in range(10):
            clock[0] += 600
            m.feel(beat(valence=0.7, event="kindness", target="Mira", agency="other"), source="story")
        rel = m.state.relationships["Mira"]
        self.assertTrue(rel["fictional"])
        self.assertFalse(rel["is_person"])
        self.assertGreater(rel["affection"], 0.05)          # she grows fond of the character
        clock[0] += 5 * 24 * 3600
        m.tick()
        self.assertNotIn("longing@Mira", m.state.emotions)
        self.assertFalse([i for i in m.intents() if i.get("target") == "Mira"])

    def test_fiction_never_feeds_desire(self):
        ap = Appraisal.from_dict(beat(erotic=0.9, romance=0.8, taboo=0.7, fulfilment=0.6,
                                      nonconsensual=True, rebuff=True))
        self.assertEqual((ap.erotic, ap.romance, ap.taboo, ap.fulfilment), (0, 0, 0, 0))
        self.assertFalse(ap.interaction)
        self.assertFalse(ap.rebuff)

    def test_fiction_is_weaker_than_life(self):
        a, _ = mind()
        b, _ = mind()
        life = a.feel(beat(fiction=False, target=None, agency="circumstance"))["effects"]["effective_intensity"]
        story = b.feel(beat(target=None, agency="circumstance"), source="story")["effects"]["effective_intensity"]
        self.assertLess(story, life)
        self.assertAlmostEqual(story, life * b.engine.t.fiction_gain, places=3)

    def test_character_never_borrows_a_real_relationship(self):
        m, clock = mind()
        m.feel({"event": "praise", "valence": 0.6, "intensity": 0.4, "agency": "other",
                "target": "Adam"}, speaker="Adam")
        trust = m.state.relationships["Adam"]["trust"]
        m.feel(beat(target="Adam", event="betrayal", valence=-0.8), source="story")
        self.assertEqual(m.state.relationships["Adam"]["trust"], trust)
        self.assertTrue(m.state.relationships["Adam (story)"]["fictional"])

    def test_read_uses_story_appraiser(self):
        fake = FakeStoryLLM([{"event": "character_death", "summary": "Mira's mentor dies",
                              "valence": -0.8, "intensity": 0.6, "relevance": 0.9,
                              "agency": "circumstance", "target": "Old Tam", "loss": True,
                              "erotic": 0.5, "agency_extra": 1}])
        llm = LLMAppraiser("http://x/v1", "m", transport=fake)
        m, clock = mind(appraiser=llm)
        res = m.read("Panel 1: the old man falls...", title="Tale")
        self.assertEqual(len(res), 1)
        ap = res[0]["appraisal"]
        self.assertTrue(ap["fiction"])
        self.assertEqual(ap["erotic"], 0)
        self.assertTrue(ap["summary"].startswith("Tale:"))
        self.assertIn("sadness", res[0]["effects"]["emotions"])
        self.assertIn("READING a story", fake.seen["messages"][0]["content"])

    def test_read_falls_back_offline(self):
        m, clock = mind()
        res = m.read("Panel 1: the hero died. Everyone is lost.", title="Tale")
        for r in res:
            self.assertTrue(r["appraisal"]["fiction"])
            self.assertLessEqual(r["appraisal"]["intensity"], 0.3)


if __name__ == "__main__":
    unittest.main()
