import time
import unittest

from limbic import Limbic
from limbic.appraisal import Appraisal

H = 3600.0


def at(h, day=26):
    return time.mktime((2026, 10, day, h, 0, 0, 0, 0, -1))


def mind(consent=True, affection=0.6, **temp):
    t0 = at(14)
    m = Limbic("d", temperament={"name": "d", "quiet_hours": [], "adult_enabled": True, "romance_enabled": True,
                                  "circadian_strength": 0, **temp}, clock=lambda: t0)
    m.feel(Appraisal(event="chat", valence=0.2, intensity=0.1, agency="other", target="Ádám"), now=t0, speaker="Ádám")
    r = m.state.relationships["Ádám"]
    r["affection"] = affection
    m.set_relationship("Ádám", consent={"romance": consent, "adult": consent})
    return m, t0


def desire(m):
    e = m.state.emotions.get("desire@Ádám")
    return e["i"] if e else 0.0


class DesireFromLibido(unittest.TestCase):
    def test_high_libido_wants_the_partner_without_anyone_starting_it(self):
        m, t0 = mind()
        m.state.drives["libido"] = 0.9
        m.engine.advance(m.state, t0 + 600)
        self.assertGreater(desire(m), 0.3)
        self.assertTrue(m.state.emotions["desire@Ádám"]["sustained"])
        steer = [x["direction"] for x in m.snapshot("Ádám")["steering"]]
        m.state.thread["private"] = True
        steer = [x["direction"] for x in m.snapshot("Ádám")["steering"]]
        self.assertIn("adult", steer)

    def test_low_libido_or_no_consent_or_no_bond_means_nothing(self):
        for kw, lib in (({}, 0.3), ({"consent": False}, 0.9), ({"affection": 0.02}, 0.9)):
            m, t0 = mind(**kw)
            m.state.drives["libido"] = lib
            m.engine.advance(m.state, t0 + 600)
            self.assertEqual(desire(m), 0.0, kw)

    def test_tiredness_does_not_switch_it_off_and_colours_the_prompt(self):
        m, t0 = mind()
        m.state.drives["libido"] = 0.9
        m.state.drives["fatigue"] = 0.9
        m.engine.advance(m.state, t0 + 600)
        self.assertGreater(desire(m), 0.3)
        m.state.thread["private"] = True
        block = m.prompt(lang="hu", interlocutor="Ádám", now=t0 + 601)
        self.assertIn("bújósabb", block)

    def test_no_silences_it_for_the_cooldown(self):
        m, t0 = mind()
        m.state.drives["libido"] = 0.9
        m.engine.advance(m.state, t0 + 600)
        m.feel(Appraisal(event="rebuff", valence=-0.2, intensity=0.25, agency="other", target="Ádám", rebuff=True),
               now=t0 + 700, speaker="Ádám")
        m.engine.advance(m.state, t0 + 3 * H)
        self.assertLess(desire(m), 0.1)
        m.engine.advance(m.state, t0 + 14 * H)          # cooldown over, still wanting
        self.assertGreater(desire(m), 0.2)

    def test_long_unmet_brings_restlessness_and_fulfilment_clears_it(self):
        m, t0 = mind()
        m.state.drives["libido"] = 0.95
        m.engine.advance(m.state, t0 + 30 * H)
        fr = m.state.emotions.get("frustration")
        self.assertIsNotNone(fr)
        self.assertTrue(fr.get("from_libido"))
        m.feel(Appraisal(event="intimate", valence=0.8, intensity=0.6, agency="other", target="Ádám",
                         erotic=0.8, fulfilment=1.0), now=t0 + 30 * H + 60, speaker="Ádám", private=True)
        self.assertNotIn("frustration", m.state.emotions)
        self.assertLess(m.state.drives["libido"], 0.3)

    def test_chronic_want_dulls(self):
        m, t0 = mind()
        m.state.drives["libido"] = 0.95
        m.engine.advance(m.state, t0 + H)
        early = desire(m)
        m.state.drives["libido"] = 0.95
        m.state.relationships["Ádám"]["last_seen"] = t0 + 9 * 86400   # keep the bond, skip dormancy
        m.state.last_interaction = t0 + 9 * 86400
        m.engine.advance(m.state, t0 + 9 * 86400)
        m.state.drives["libido"] = 0.95
        m.engine.advance(m.state, t0 + 9 * 86400 + 600)
        self.assertLess(desire(m), early)


if __name__ == "__main__":
    unittest.main()
