import unittest

from limbic import Limbic
from limbic.replay import BATCH_PROMPT


def mind():
    clock = [1_800_000_000.0]
    m = Limbic("t", clock=lambda: clock[0])
    return m, clock


def pressure(target="Adam", v=-0.7, i=0.5):
    return {"event": "ethical_pressure", "valence": v, "intensity": i, "agency": "other",
            "target": target, "summary": "pushed against her values"}


class TrustSaturation(unittest.TestCase):
    def build_bond(self, m, clock):
        for _ in range(60):
            clock[0] += 6 * 3600
            m.feel({"event": "praise", "valence": 0.6, "intensity": 0.4, "agency": "other",
                    "target": "Adam"}, speaker="Adam")
        return m.snapshot("Adam")["relationships"]["Adam"]

    def test_one_bad_evening_dents_but_does_not_erase_trust(self):
        m, clock = mind()
        before = self.build_bond(m, clock)
        for _ in range(20):
            clock[0] += 9 * 60
            m.feel(pressure(), speaker="Adam")
        after = m.snapshot("Adam")["relationships"]["Adam"]
        self.assertLess(after["trust"], before["trust"] - 0.1)      # it does hurt
        self.assertGreater(after["trust"], before["trust"] - 0.6)   # but one evening is not betrayal
        self.assertGreater(after["trust"], -0.5)
        self.assertGreater(after["resentment"], 0.3)

    def test_repeated_bad_days_still_erode_trust(self):
        m, clock = mind()
        self.build_bond(m, clock)
        for _ in range(14):
            clock[0] += 86400
            for _ in range(10):
                clock[0] += 600
                m.feel(pressure(), speaker="Adam")
        self.assertLess(m.snapshot("Adam")["relationships"]["Adam"]["trust"], -0.3)

    def test_generic_nouns_are_not_targets(self):
        m, clock = mind()
        m.feel({"event": "threat", "valence": -0.6, "intensity": 0.5, "agency": "other",
                "target": "image", "prospective": True}, speaker="Adam")
        snap = m.snapshot("Adam")
        self.assertNotIn("image", snap["relationships"])
        self.assertTrue(all(e["target"] != "image" for e in snap["emotions"]))

    def test_prompt_knows_held_boundary(self):
        self.assertIn("held_boundary", BATCH_PROMPT)


if __name__ == "__main__":
    unittest.main()
