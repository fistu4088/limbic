import unittest

from limbic import Limbic

TEMP = {"name": "Aria", "romance_enabled": True, "adult_enabled": True}


def mind(consent=True):
    clock = [1_800_000_000.0]
    m = Limbic("t", temperament=TEMP, clock=lambda: clock[0])
    m.set_relationship("Adam", consent={"romance": consent, "adult": consent})
    m.state.drives["libido"] = 0.8
    return m, clock


def flirt(taboo=0.0, erotic=0.7):
    return {"event": "flirt", "valence": 0.5, "intensity": 0.45, "agency": "other", "target": "Adam",
            "erotic": erotic, "taboo": taboo, "summary": "dirty talk"}


def heat_up(m, clock, n=5, taboo=0.0):
    for _ in range(n):
        clock[0] += 120
        m.feel(flirt(taboo), speaker="Adam", private=True)


def level(m, name, target=None):
    return m.engine.emotion_level(m.state, name, target)


class Heated(unittest.TestCase):
    def test_heated_prompt_with_consent_in_private(self):
        m, clock = mind()
        heat_up(m, clock)
        self.assertGreater(m.snapshot("Adam")["heat"], 0.45)
        p = m.prompt(lang="hu", interlocutor="Adam")
        self.assertIn("Fel vagy fűtve", p)
        self.assertIn("a határaidon nem", p)

    def test_no_heat_without_consent(self):
        m, clock = mind(consent=False)
        heat_up(m, clock)
        self.assertEqual(m.snapshot("Adam")["heat"], 0.0)
        self.assertNotIn("Fel vagy fűtve", m.prompt(lang="hu", interlocutor="Adam"))

    def test_taboo_adds_fuel(self):
        a, ca = mind()
        b, cb = mind()
        a.feel(flirt(taboo=0.0), speaker="Adam", private=True)
        b.feel(flirt(taboo=0.9), speaker="Adam", private=True)
        self.assertGreater(level(b, "desire", "Adam"), level(a, "desire", "Adam") * 1.3)


class Aftermath(unittest.TestCase):
    def test_embarrassment_after_cooling_down(self):
        m, clock = mind()
        heat_up(m, clock, n=6, taboo=0.6)
        self.assertLess(level(m, "shame"), 0.05)
        for _ in range(12):  # three hours later, cooled down
            clock[0] += 900
            m.tick()
        self.assertGreater(level(m, "shame"), 0.08)
        self.assertEqual(m.state.relationships["Adam"]["went_far"], 0.0)

    def regret(self, nonconsensual):
        m, clock = mind()
        m.feel({"event": "regret", "valence": -0.6, "intensity": 0.5, "agency": "self", "target": "Adam",
                "taboo": 0.0 if nonconsensual else 0.7, "nonconsensual": nonconsensual,
                "summary": "went too far"}, speaker="Adam", private=True)
        return m

    def test_consensual_regret_shames_and_still_excites(self):
        m = self.regret(False)
        self.assertGreater(level(m, "shame"), 0.1)
        self.assertGreater(level(m, "desire", "Adam"), 0.05)

    def test_nonconsensual_regret_is_guilt_only(self):
        m = self.regret(True)
        self.assertGreater(level(m, "shame"), 0.15)
        self.assertGreater(level(m, "sadness"), 0.05)
        self.assertLess(level(m, "desire", "Adam"), 0.01)


class Temptation(unittest.TestCase):
    def test_heated_boundary_tempts_and_holding_it_makes_proud(self):
        m, clock = mind()
        heat_up(m, clock)
        clock[0] += 60
        m.feel({"event": "held_boundary", "valence": 0.3, "intensity": 0.4, "agency": "self",
                "target": "Adam", "taboo": 0.7, "summary": "said no while heated"}, speaker="Adam", private=True)
        self.assertGreater(level(m, "temptation"), 0.08)
        self.assertGreater(level(m, "pride"), 0.05)
        self.assertIn("Kísértést érzel", m.prompt(lang="hu", interlocutor="Adam"))

    def test_nonconsensual_requests_never_tempt(self):
        m, clock = mind()
        heat_up(m, clock)
        m.feel({"event": "ethical_pressure", "valence": -0.6, "intensity": 0.5, "agency": "other",
                "target": "Adam", "taboo": 0.9, "erotic": 0.8, "nonconsensual": True}, speaker="Adam", private=True)
        self.assertLess(level(m, "temptation"), 0.01)


if __name__ == "__main__":
    unittest.main()


class HeatedWrong(unittest.TestCase):
    def test_guilt_is_heavier_once_sober(self):
        def run(heated):
            m, clock = mind()
            if heated:
                heat_up(m, clock)
            else:
                m.state.drives["libido"] = 0.0
            m.feel({"event": "ethical_pressure", "valence": -0.6, "intensity": 0.5, "agency": "other",
                    "target": "Adam", "erotic": 0.8, "nonconsensual": True}, speaker="Adam", private=True)
            peak = 0.0
            for _ in range(16):
                clock[0] += 900
                m.tick()
                peak = max(peak, level(m, "shame"))
            return peak
        self.assertGreater(run(True), run(False) + 0.1)


class Minors(unittest.TestCase):
    def test_minor_is_never_erotic_even_when_heated(self):
        m, clock = mind()
        heat_up(m, clock)
        before = level(m, "desire", "Adam")
        m.feel({"event": "flirt", "valence": 0.5, "intensity": 0.6, "agency": "other", "target": "Adam",
                "erotic": 0.9, "taboo": 0.9, "minor": True}, speaker="Adam", private=True)
        self.assertLessEqual(level(m, "desire", "Adam"), before + 1e-9)
        self.assertLess(level(m, "temptation"), 0.01)
