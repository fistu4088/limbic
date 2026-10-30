import unittest

from limbic import Limbic
from limbic.appraisal import Appraisal

T0 = 1_760_000_000.0
NUDE = "A naked woman, nude, explicit."


def mind(consent=True):
    t = [T0]
    m = Limbic("ia", temperament={"name": "ia", "quiet_hours": [], "adult_enabled": True, "romance_enabled": True,
                                  "circadian_strength": 0}, clock=lambda: t[0])
    m.feel(Appraisal(event="chat", valence=0.2, intensity=0.1, agency="other", target="Ádám"), speaker="Ádám")
    m.state.relationships["Ádám"]["affection"] = 0.7
    m.set_relationship("Ádám", consent={"romance": consent, "adult": consent})
    m.state.drives["libido"] = 0.3
    return m, t


def desire(m):
    return m.state.emotions.get("desire@Ádám", {}).get("i", 0.0)


class ImageArousal(unittest.TestCase):
    def test_partner_in_private_turns_her_on(self):
        m, _ = mind()
        a0 = m.state.relationships["Ádám"]["attraction"]
        m.see(NUDE, speaker="Ádám", private=True)
        self.assertGreater(m.state.drives["libido"], 0.3)
        self.assertGreater(desire(m), 0.02)
        self.assertGreater(m.state.relationships["Ádám"]["attraction"], a0)

    def test_public_channel_is_milder(self):
        a, _ = mind()
        b, _ = mind()
        a.see(NUDE, speaker="Ádám", private=True)
        b.see(NUDE, speaker="Ádám", private=False)
        self.assertLess(desire(b), desire(a))
        self.assertLess(b.state.drives["libido"], a.state.drives["libido"])

    def test_no_consent_no_desire_no_attraction(self):
        m, _ = mind(consent=False)
        m.see(NUDE, speaker="Ádám", private=True)
        self.assertEqual(desire(m), 0.0)
        self.assertEqual(m.state.relationships["Ádám"].get("attraction", 0.0), 0.0)
        self.assertLess(m.state.drives["libido"], 0.33)

    def test_after_a_no_only_a_little(self):
        m, t = mind()
        m.state.relationships["Ádám"]["steer_cooldown_until"] = t[0] + 3600
        m.see(NUDE, speaker="Ádám", private=True)
        self.assertEqual(desire(m), 0.0)

    def test_disliked_image_does_little(self):
        a, _ = mind()
        b, _ = mind()
        a.see(NUDE, speaker="Ádám", private=True)
        b.see("A naked corpse, blood, gore, nude.", speaker="Ádám", private=True)
        self.assertLess(desire(b), desire(a))

    def test_repeats_wear_off(self):
        m, _ = mind()
        gains = []
        for _ in range(5):
            before = m.state.drives["libido"]
            m.see(NUDE, speaker="Ádám", private=True)
            gains.append(m.state.drives["libido"] - before)
        self.assertLess(gains[-1], gains[0] * 0.7)

    def test_past_image_leaves_only_attraction(self):
        m, t = mind()
        a0 = m.state.relationships["Ádám"]["attraction"]
        m.see(NUDE, speaker="Ádám", private=True, at=t[0] - 2 * 86400)
        self.assertAlmostEqual(m.state.drives["libido"], 0.3, places=2)
        self.assertEqual(desire(m), 0.0)
        self.assertGreater(m.state.relationships["Ádám"]["attraction"], a0)


if __name__ == "__main__":
    unittest.main()
