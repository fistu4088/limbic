import time
import unittest

from limbic import Limbic
from limbic.appraisal import Appraisal

H = 3600.0


def mind(consent=True):
    t0 = time.mktime((2026, 10, 26, 14, 0, 0, 0, 0, -1))
    m = Limbic("o", temperament={"name": "o", "quiet_hours": [], "adult_enabled": True, "romance_enabled": True,
                                  "circadian_strength": 0}, clock=lambda: t0)
    m.feel(Appraisal(event="chat", valence=0.2, intensity=0.1, agency="other", target="Ádám"), now=t0, speaker="Ádám")
    m.state.relationships["Ádám"]["affection"] = 0.8
    m.set_relationship("Ádám", consent={"romance": consent, "adult": consent})
    return m, t0


def good(m, t, ful=0.0):
    return m.feel(Appraisal(event="intimate", valence=0.6, intensity=0.5, agency="other", target="Ádám",
                            erotic=0.7, romance=0.4, fulfilment=ful), now=t, speaker="Ádám", private=True)


def push(m, t):
    return m.feel(Appraisal(event="pressure", valence=-0.4, intensity=0.4, agency="other", target="Ádám",
                            erotic=0.5, rebuff=True), now=t, speaker="Ádám", private=True)


def o(m):
    return m.state.sexuality.get("openness", 0.0)


class Learning(unittest.TestCase):
    def test_good_moments_teach_more_with_high_libido_and_fulfilment(self):
        lo, t0 = mind()
        hi, _ = mind()
        lo.state.drives["libido"] = 0.1
        hi.state.drives["libido"] = 0.9
        for i in range(5):
            good(lo, t0 + 600 * (i + 1))
            hi.state.drives["libido"] = 0.9
            good(hi, t0 + 600 * (i + 1), ful=0.6 if i == 4 else 0.0)
        self.assertGreater(o(lo), 0.0)
        self.assertGreater(o(hi), 1.5 * o(lo))
        self.assertGreater(hi.state.relationships["Ádám"]["attraction"], lo.state.relationships["Ádám"]["attraction"])

    def test_pressure_past_her_no_unlearns_harder(self):
        m, t0 = mind()
        m.state.drives["libido"] = 0.9
        for i in range(4):
            good(m, t0 + 600 * (i + 1))
        before = o(m)
        push(m, t0 + 3000)
        self.assertLess(o(m), before)
        self.assertGreater(before - o(m), (before / 4) * 1.5)   # one push undoes more than one good moment adds

    def test_own_boundary_is_not_their_no(self):
        m, t0 = mind()
        r = m.feel(Appraisal(event="held_boundary", valence=0.2, intensity=0.3, agency="self", target="Ádám",
                             rebuff=True), now=t0 + 60, speaker="Ádám", private=True)
        self.assertNotIn("shame", r["effects"]["emotions"])
        self.assertTrue(r["effects"].get("own_boundary"))

    def test_libido_refills_faster_when_open(self):
        a, t0 = mind()
        b, _ = mind()
        b.state.sexuality["openness"] = 0.8
        for m in (a, b):
            m.state.drives["libido"] = 0.0
            m.engine.advance(m.state, t0 + 12 * H)
        self.assertGreater(b.state.drives["libido"], 1.6 * a.state.drives["libido"])

    def test_past_the_point_wants_closeness_even_with_low_libido(self):
        closed, t0 = mind()
        opened, _ = mind()
        opened.state.sexuality["openness"] = 0.85
        for m in (closed, opened):
            m.state.relationships["Ádám"]["attraction"] = 0.6
            m.state.drives["libido"] = 0.2
            m.engine.advance(m.state, t0 + 600)
            m.state.drives["libido"] = 0.2
            m.engine.advance(m.state, t0 + 900)
        self.assertNotIn("desire@Ádám", closed.state.emotions)
        self.assertGreater(opened.state.emotions["desire@Ádám"]["i"], 0.05)

    def test_no_consent_no_learning(self):
        m, t0 = mind(consent=False)
        m.state.drives["libido"] = 0.9
        for i in range(5):
            good(m, t0 + 600 * (i + 1))
        self.assertEqual(o(m), 0.0)

    def test_openness_fades_slowly(self):
        m, t0 = mind()
        m.state.sexuality["openness"] = 0.6
        m.engine.advance(m.state, t0 + 45 * 86400)
        self.assertAlmostEqual(o(m), 0.3, delta=0.03)


if __name__ == "__main__":
    unittest.main()
