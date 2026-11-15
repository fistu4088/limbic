import random
import unittest

from limbic import Limbic
from limbic import rules, tastes

H = 3600.0
DAY = 86400.0
T0 = 1_760_000_000.0


class Bag:
    """Minimal agent state for the pure taste functions."""
    def __init__(self):
        self.leisure = {}


def mind():
    return Limbic("teszt", temperament={"name": "teszt", "quiet_hours": []}, clock=lambda: T0)


class HardRuleTests(unittest.TestCase):
    def test_minor_sexual_never_stored(self):
        s = Bag()
        self.assertIsNone(tastes.record(s, "valami", 0.9, 1.0, T0, tags=["minor", "sexual"]))
        self.assertEqual(tastes._store(s), {})

    def test_nonconsent_sexual_never_stored(self):
        s = Bag()
        self.assertIsNone(tastes.record(s, "valami", 0.9, 1.0, T0, tags=["nonconsensual", "erotic"]))

    def test_neutral_minor_is_fine(self):
        self.assertTrue(rules.taste_allowed(["minor"]))

    def test_leisure_is_read_only(self):
        rules.check_leisure_action("search")
        rules.check_leisure_action("read")
        for bad in ("post", "register", "write", "shell"):
            with self.assertRaises(PermissionError):
                rules.check_leisure_action(bad)


class TasteTests(unittest.TestCase):
    def test_experience_moves_taste(self):
        s = Bag()
        tastes.record(s, "Pszichológiai thriller", 0.8, 1.0, T0)
        self.assertGreater(tastes.score(s, "pszichológiai thriller", T0), 0.0)

    def test_negative_experience_is_stronger(self):
        a, b = Bag(), Bag()
        tastes.record(a, "x", 0.5, 1.0, T0)
        tastes.record(b, "x", -0.5, 1.0, T0)
        self.assertGreater(abs(tastes._store(b)["x"]["w"]), abs(tastes._store(a)["x"]["w"]))

    def test_habituation_repeats_move_less(self):
        s = Bag()
        steps = []
        for _ in range(5):
            before = tastes._store(s).get("y", {}).get("w", 0.0)
            tastes.record(s, "y", 0.9, 1.0, T0)
            steps.append(tastes._store(s)["y"]["w"] - before)
        self.assertGreater(steps[0], steps[-1])

    def test_prior_is_weak_and_overridden_by_experience(self):
        s = Bag()
        tastes.seed(s, [{"topic": "horror", "w": 0.9}], T0)
        self.assertLess(abs(tastes.score(s, "horror", T0)), 0.25)
        for _ in range(4):
            tastes.record(s, "horror", -0.9, 1.0, T0)
        self.assertLess(tastes.score(s, "horror", T0), -0.2)

    def test_unexperienced_prior_fades(self):
        s = Bag()
        tastes.seed(s, [{"topic": "anime", "w": 0.9}], T0)
        fresh = tastes.score(s, "anime", T0)
        later = tastes.score(s, "anime", T0 + 60 * DAY)
        self.assertLess(later, fresh * 0.4)

    def test_seed_never_overwrites_experience(self):
        s = Bag()
        tastes.record(s, "kód", -0.7, 1.0, T0)
        tastes.seed(s, [{"topic": "kód", "w": 0.9}], T0)
        self.assertLess(tastes._store(s)["kód"]["w"], 0)

    def test_single_refusal_is_not_a_boundary(self):
        s = Bag()
        tastes.record(s, "spoiler", -1.0, 1.0, T0)
        self.assertFalse(tastes.is_boundary(s, "spoiler"))
        self.assertGreater(tastes.bias(s, "spoiler", T0), 0.0)

    def test_repeated_refusals_lower_but_never_ban(self):
        s = Bag()
        for _ in range(8):
            tastes.record(s, "lobotomia", -0.9, 1.0, T0)
        self.assertFalse(tastes.is_boundary(s, "lobotomia"))
        self.assertLess(tastes.score(s, "lobotomia", T0), -0.3)
        self.assertGreater(tastes.bias(s, "lobotomia", T0), 0.0)

    def test_explicit_stop_makes_fixed_boundary_that_does_not_fade(self):
        s = Bag()
        tastes.record(s, "spoiler", -1.0, 1.0, T0, explicit_stop=True)
        self.assertTrue(tastes.is_boundary(s, "spoiler"))
        self.assertEqual(tastes.bias(s, "spoiler", T0), 0.0)
        self.assertLessEqual(tastes.score(s, "spoiler", T0 + 365 * DAY), -0.8)

    def test_bad_subtype_does_not_poison_genre(self):
        s = Bag()
        for _ in range(4):
            tastes.record(s, "horror/jumpscare", -0.9, 1.0, T0)
        for _ in range(4):
            tastes.record(s, "horror/pszichologiai", 0.8, 1.0, T0)
        self.assertLess(tastes.score(s, "horror/jumpscare", T0), -0.3)
        self.assertGreater(tastes.score(s, "horror/pszichologiai", T0), 0.3)
        self.assertGreaterEqual(tastes.score(s, "horror", T0), -0.3)

    def test_prior_boundary_is_soft_not_fixed(self):
        s = Bag()
        tastes.seed(s, [{"topic": "tilos", "kind": "boundary"}], T0)
        self.assertFalse(tastes.is_boundary(s, "tilos"))

    def test_pick_skips_boundaries_and_forbidden(self):
        s = Bag()
        tastes.record(s, "tilos", -1.0, 1.0, T0, explicit_stop=True)
        cands = [{"topic": "tilos"}, {"topic": "ok"}, {"topic": "rossz", "tags": ["minor", "sexual"]}]
        rng = random.Random(0)
        picks = {tastes.pick(s, cands, T0, rng=rng) for _ in range(50)}
        self.assertEqual(picks, {"ok"})

    def test_pick_prefers_liked(self):
        s = Bag()
        for _ in range(4):
            tastes.record(s, "szeretett", 0.9, 1.0, T0)
        for _ in range(4):
            tastes.record(s, "nem", -0.7, 1.0, T0)
        cands = [{"topic": "szeretett"}, {"topic": "nem"}]
        rng = random.Random(1)
        counts = {"szeretett": 0, "nem": 0}
        for _ in range(400):
            choice = tastes.pick(s, cands, T0, novelty=0.0, rng=rng) or ""
            if choice in counts:
                counts[choice] += 1
        # a soft bias, not a ban: the disliked one still comes up now and then
        self.assertGreater(counts["szeretett"], 1.3 * counts["nem"])
        self.assertGreater(counts["nem"], 0)

    def test_summary_splits_interests_dislikes_boundaries(self):
        s = Bag()
        for _ in range(4):
            tastes.record(s, "a", 0.9, 1.0, T0)
            tastes.record(s, "b", -0.9, 1.0, T0)
        tastes.record(s, "c", -1.0, 1.0, T0, explicit_stop=True)
        out = tastes.summary(s, T0)
        self.assertEqual([r["topic"] for r in out["interests"]], ["a"])
        self.assertEqual([r["topic"] for r in out["dislikes"]], ["b"])
        self.assertEqual([r["topic"] for r in out["boundaries"]], ["c"])


class LeisureIntegrationTests(unittest.TestCase):
    def test_finished_activity_teaches_taste_for_its_topic(self):
        m = mind()
        m.leisure_start("lector", topic="pszichológiai thriller", now=T0)
        cur = m.state.leisure["current"]
        cur["steps"], cur["val_sum"], cur["val_n"] = 3, 0.9, 1
        m.leisure_finish(rating=0.8, now=T0 + H)
        self.assertGreater(tastes.score(m.state, "pszichológiai thriller", T0 + H), 0.0)

    def test_abandoned_or_zero_step_teaches_nothing(self):
        m = mind()
        m.leisure_start("web", topic="semmi", now=T0)
        m.leisure_finish(rating=0.8, now=T0 + H)
        self.assertNotIn("semmi", tastes._store(m.state))


if __name__ == "__main__":
    unittest.main()


class LeisureEffectTests(unittest.TestCase):
    def test_information_actions_allowed(self):
        from limbic import rules
        for a in ("search", "read", "look_image", "note"):
            rules.check_leisure_action(a)

    def test_effectful_actions_refused(self):
        from limbic import rules
        for a in ("write", "post", "register", "delete", "self_delete", "download",
                  "run_code", "share_data", "config_change", "send_message"):
            with self.assertRaises(PermissionError):
                rules.check_leisure_action(a)

    def test_unknown_action_refused(self):
        from limbic import rules
        with self.assertRaises(PermissionError):
            rules.check_leisure_action("valami_uj")

    def test_code_activity_removed(self):
        from limbic import leisure
        self.assertNotIn("code", leisure.ACTIVITIES)


class GradualTasteTests(unittest.TestCase):
    def test_forbid_is_the_only_way_to_fixed_boundary(self):
        s = Bag()
        tastes.record(s, "x", -1.0, 1.0, T0)
        self.assertFalse(tastes.is_boundary(s, "x"))
        tastes.forbid(s, "x", T0)
        self.assertTrue(tastes.is_boundary(s, "x"))

    def test_three_good_experiences_make_an_interest(self):
        s = Bag()
        for _ in range(3):
            tastes.record(s, "sci-fi", 0.8, 1.0, T0)
        out = tastes.summary(s, T0)
        self.assertIn("sci-fi", [r["topic"] for r in out["interests"]])
