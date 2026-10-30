import unittest

from limbic import Limbic


class SeeingImages(unittest.TestCase):
    def setUp(self):
        self.t = [1_760_000_000.0]
        self.m = Limbic("img", temperament={"name": "img", "quiet_hours": [], "likes": ["anime"],
                                            "circadian_strength": 0}, clock=lambda: self.t[0])

    def test_cute_image_lifts_mood_and_is_remembered(self):
        r = self.m.see("A cute kitten smiling on a sofa.", speaker="Ádám")
        self.t[0] += 600
        self.m.tick()
        self.assertGreater(self.m.state.mood["p"], 0.0)
        self.assertTrue(r[0]["effects"]["emotions"])
        ev = self.m.store.events("img")
        self.assertEqual(ev[-1]["source"], "image")
        self.assertIn("láttam egy képet", ev[-1]["appraisal"]["summary"])
        self.assertFalse(r[0]["appraisal"]["interaction"])

    def test_gore_feels_bad(self):
        self.m.see("A corpse with a deep stab wound, blood everywhere.")
        self.t[0] += 600
        self.m.tick()
        self.assertLess(self.m.state.mood["p"], 0.0)

    def test_image_never_touches_relationships_or_desire(self):
        self.m.see("A naked woman, nude, explicit.", speaker="Ádám", private=True)
        rel = self.m.state.relationships.get("Ádám")
        self.assertTrue(rel is None or (rel["affection"] == 0 and rel.get("attraction", 0) == 0))
        self.assertFalse(any(k.startswith("desire") for k in self.m.state.emotions))
        self.assertEqual(self.m.state.last_interaction, self.m.state.last_interaction)

    def test_not_social_contact(self):
        self.m.state.drives["social"] = 0.7
        self.m.see("A cute puppy.", speaker="Ádám")
        self.assertGreaterEqual(self.m.state.drives["social"], 0.69)

    def test_past_image_is_dated_back_and_weaker(self):
        a = Limbic("a", temperament={"name": "a", "quiet_hours": [], "circadian_strength": 0}, clock=lambda: self.t[0])
        b = Limbic("b", temperament={"name": "b", "quiet_hours": [], "circadian_strength": 0}, clock=lambda: self.t[0])
        ra = a.see("A corpse, blood, gore.")
        rb = b.see("A corpse, blood, gore.", at=self.t[0] - 3 * 86400)
        self.assertLess(sum(rb[0]["effects"]["emotions"].values()), sum(ra[0]["effects"]["emotions"].values()))
        ev = b.store.events("b")[-1]
        self.assertAlmostEqual(ev["t"], self.t[0] - 3 * 86400, delta=1)
        self.assertIn("korábbi emlék", ev["appraisal"]["summary"])


if __name__ == "__main__":
    unittest.main()
