import os, tempfile, unittest

from limbic.agent import Limbic as Agent
from limbic.store import Store
from limbic.temperament import Temperament


def mk(partners=None, aliases=None):
    d = tempfile.mkdtemp()
    st = Store(os.path.join(d, "t.db"))
    T = Temperament(name="Aria", partners=list(partners or []), aliases=dict(aliases or {}))
    clock = [1_000_000.0]
    a = Agent("aria", st, temperament=T, clock=lambda: clock[0])
    return a, clock


AP = {"event": "praise", "valence": 0.7, "intensity": 0.6, "agency": "other", "affinity": 0.5}


class Partners(unittest.TestCase):
    def test_open_by_default(self):
        a, _ = mk()
        a.feel(dict(AP, target="Béla"), speaker="Béla")
        self.assertIn("Béla", a.state.relationships)

    def test_only_partners_bond(self):
        a, _ = mk(partners=["Ádám"])
        a.feel(dict(AP, target="Béla"), speaker="Béla")
        self.assertNotIn("Béla", a.state.relationships)
        self.assertFalse(any(e.get("target") == "Béla" for e in a.state.emotions.values()))
        a.feel(dict(AP, target="Ádám"), speaker="Ádám")
        self.assertGreater(a.state.relationships["Ádám"]["affection"], 0)

    def test_fiction_and_things_get_no_bond(self):
        a, _ = mk(partners=["Ádám"])
        a.feel(dict(AP, target="Hero", fiction=True), speaker="Ádám")
        a.feel(dict(AP, target="Proxmox"), speaker="Ádám")
        self.assertEqual(set(a.state.relationships), {"Ádám"})

    def test_alias_is_same_person(self):
        a, _ = mk(partners=["Ádám"], aliases={"uid:0": "Ádám"})
        a.feel(dict(AP, target="uid:0"), speaker="uid:0")
        self.assertEqual(set(a.state.relationships), {"Ádám"})
        self.assertIn("praise", a.prompt(interlocutor="uid:0") + "praise")  # no crash

    def test_merge_moves_everything(self):
        a, _ = mk()
        a.feel(dict(AP, target="Ádám"), speaker="Ádám")
        a.feel(dict(AP, target="uid:0"), speaker="uid:0")
        before = a.state.relationships["Ádám"]["interactions"] + a.state.relationships["uid:0"]["interactions"]
        a.state.relationships["Ádám"]["consent"]["romance"] = True
        out = a.merge_relationship("uid:0", "Ádám")
        r = a.state.relationships["Ádám"]
        self.assertNotIn("uid:0", a.state.relationships)
        self.assertEqual(r["interactions"], before)
        self.assertTrue(r["consent"]["romance"])
        self.assertFalse(any(e.get("target") == "uid:0" for e in a.state.emotions.values()))
        self.assertGreaterEqual(out["events"], 1)
        rows = a.store.db.execute("SELECT count(*) FROM events WHERE speaker='uid:0'").fetchone()[0]
        self.assertEqual(rows, 0)
        self.assertEqual(a.temperament.aliases["uid:0"], "Ádám")

    def test_add_remove_prune(self):
        a, _ = mk()
        a.feel(dict(AP, target="Béla"), speaker="Béla")
        a.add_partner("Ádám")
        a.prune_relationships()
        self.assertEqual(set(a.state.relationships), {"Ádám"})
        a.add_partner("Béla")
        a.feel(dict(AP, target="Béla"), speaker="Béla")
        self.assertIn("Béla", a.state.relationships)
        a.remove_partner("Béla")
        self.assertNotIn("Béla", a.state.relationships)
        with self.assertRaises(ValueError):
            a.remove_partner("Ádám")   # never back to "open" by accident


if __name__ == "__main__":
    unittest.main()
