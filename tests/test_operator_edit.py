import unittest

from limbic import Limbic
from limbic.appraisal import Appraisal


class OperatorEdit(unittest.TestCase):
    def setUp(self):
        self.t = [1_760_000_000.0]
        self.m = Limbic("op", temperament={"name": "op", "quiet_hours": []}, clock=lambda: self.t[0])
        self.m.feel(Appraisal(event="chat", valence=0.2, intensity=0.1, agency="other", target="Ádám"),
                    now=self.t[0], speaker="Ádám")

    def test_levels_are_set_clamped_and_logged(self):
        r = self.m.set_relationship("Ádám", affection=0.7, trust=-3, fear=0.2, attraction=2)
        self.assertEqual(r["affection"], 0.7)
        self.assertEqual(r["trust"], -1.0)
        self.assertEqual(r["attraction"], 1.0)
        ev = self.m.store.events("op", kind="operator_edit")
        self.assertEqual(len(ev), 1)
        self.assertEqual(ev[0]["effects"]["changed"]["affection"]["to"], 0.7)

    def test_no_change_no_log(self):
        self.m.set_relationship("Ádám", reachable=True)
        self.assertEqual(self.m.store.events("op", kind="operator_edit"), [])

    def test_clearing_longing_quiets_the_feeling(self):
        r = self.m.state.relationships["Ádám"]
        r["affection"] = 0.9
        self.t[0] += 2 * 86400
        self.m.tick()
        self.assertIn("longing@Ádám", self.m.state.emotions)
        self.m.set_relationship("Ádám", longing=0.0)
        self.assertFalse(self.m.state.emotions["longing@Ádám"]["sustained"])

    def test_set_and_clear_emotion(self):
        self.m.set_emotion("joy", None, 0.6)
        self.assertAlmostEqual(self.m.state.emotions["joy"]["i"], 0.6)
        self.m.set_emotion("tenderness", "Ádám", 0.4)
        self.assertIn("tenderness@Ádám", self.m.state.emotions)
        self.m.set_emotion("joy", None, 0)
        self.assertNotIn("joy", self.m.state.emotions)
        self.t[0] += 6 * 3600
        self.m.tick()
        self.assertLess(self.m.state.emotions.get("tenderness@Ádám", {"i": 0})["i"], 0.4)  # fades normally
        self.assertEqual(len(self.m.store.events("op", kind="operator_edit")), 3)

    def test_adult_feeling_needs_enabled(self):
        with self.assertRaises(ValueError):
            self.m.set_emotion("desire", "Ádám", 0.5)

    def test_drives(self):
        r = self.m.set_drives(social=0.8, novelty=2, bogus=0.5)
        self.assertEqual(r["social"], 0.8)
        self.assertEqual(r["novelty"], 1.0)
        self.assertNotIn("bogus", r)

    def test_mood(self):
        r = self.m.set_mood(p=0.7, baseline_p=0.3, x=1)
        self.assertEqual(r["mood"]["p"], 0.7)
        self.assertAlmostEqual(r["baseline"]["p"], max(self.m.engine.t.baseline_min, min(self.m.engine.t.baseline_max, 0.3)))

    def test_openness(self):
        self.assertEqual(self.m.set_sexuality(0.6)["openness"], 0.6)
        self.assertEqual(self.m.set_sexuality(5)["openness"], 1.0)


if __name__ == "__main__":
    unittest.main()
