import json
import math
import threading
import unittest
import urllib.request

from limbic import Appraisal, Engine, Limbic, LLMAppraiser, Store, Temperament
from limbic.appraisal import KeywordAppraiser, map_emotions, parse_appraisals
from limbic.engine import ladder_index

T0 = 1_700_000_000.0
PRAISE = dict(event="praise", valence=0.7, intensity=0.6, agency="other", target="Ann", relevance=0.7)
INSULT = dict(event="insult", valence=-0.7, intensity=0.6, agency="other", target="Bob", relevance=0.7)


def fresh(**temp):
    clock = [T0]
    mind = Limbic("t", store=Store(":memory:"), temperament=temp or {}, clock=lambda: clock[0])
    return mind, clock


class Decay(unittest.TestCase):
    def test_emotion_decays_and_disappears(self):
        m, c = fresh()
        m.feel(PRAISE)
        j0 = m.engine.emotion_level(m.state, "joy")
        c[0] += 3600
        m.tick()
        j1 = m.engine.emotion_level(m.state, "joy")
        self.assertLess(j1, j0)
        c[0] += 86400
        m.tick()
        self.assertEqual(m.engine.emotion_level(m.state, "joy"), 0.0)

    def test_strong_peaks_fade_faster_relative(self):
        e = Engine()
        s1, s2 = e.new_state(T0), e.new_state(T0)
        s1.emotions["joy"] = {"name": "joy", "target": None, "i": 0.9, "cause": ""}
        s2.emotions["joy"] = {"name": "joy", "target": None, "i": 0.2, "cause": ""}
        e.advance(s1, T0 + 1800)
        e.advance(s2, T0 + 1800)
        self.assertLess(s1.emotions["joy"]["i"] / 0.9, s2.emotions["joy"]["i"] / 0.2)

    def test_mood_returns_to_baseline(self):
        m, c = fresh()
        m.feel(PRAISE)
        self.assertGreater(m.state.mood["p"], 0.05)
        c[0] += 2 * 86400
        m.tick()
        self.assertAlmostEqual(m.state.mood["p"], m.state.baseline["p"], delta=0.03)


class Rules(unittest.TestCase):
    def test_praise_makes_joy_and_gratitude_towards_target(self):
        m, _ = fresh()
        r = m.feel(PRAISE)
        self.assertIn("joy", r["effects"]["emotions"])
        self.assertIn("gratitude@Ann", m.state.emotions)
        self.assertGreater(m.state.relationships["Ann"]["affection"], 0)

    def test_insult_angers_confident_agent_frightens_cornered_one(self):
        ap = Appraisal(**INSULT)
        self.assertIn("anger", map_emotions(ap, {"p": 0, "a": 0, "d": 0.2}))
        cornered = map_emotions(ap, {"p": 0, "a": 0, "d": -0.6})
        self.assertIn("fear", cornered)
        self.assertNotIn("anger", cornered)

    def test_loss_grieves_without_grudge(self):
        m, _ = fresh()
        m.feel(dict(event="loss", valence=-0.8, intensity=0.7, loss=True, target="Friend",
                    agency="circumstance", relevance=0.9))
        self.assertIn("sadness@Friend", m.state.emotions)
        self.assertEqual(m.state.relationships["Friend"]["resentment"], 0.0)

    def test_killer_gets_resentment(self):
        m, _ = fresh()
        m.feel(dict(event="harm", valence=-0.8, intensity=0.7, agency="other", target="Killer"))
        self.assertGreater(m.state.relationships["Killer"]["resentment"], 0.1)
        self.assertLess(m.state.relationships["Killer"]["trust"], 0)

    def test_prospective_threat_is_fear(self):
        self.assertIn("fear", map_emotions(Appraisal(valence=-0.6, prospective=True), {"d": 0}))
        self.assertIn("hope", map_emotions(Appraisal(valence=0.6, prospective=True), {"d": 0}))


class SmallTalkCeiling(unittest.TestCase):
    def test_small_talk_never_lifts_above_baseline(self):
        m, c = fresh()
        for _ in range(50):
            c[0] += 600
            m.feel(dict(event="chat", valence=0.1, intensity=0.05, agency="other", target="Ann"))
        self.assertLessEqual(m.state.mood["p"], m.state.baseline["p"] + 0.02)
        self.assertEqual(m.state.mood_index, ladder_index(0.0))

    def test_small_talk_does_lift_bad_mood_back_up(self):
        m, c = fresh()
        m.state.mood["p"] = -0.4
        before = m.state.mood["p"]
        m.feel(dict(event="chat", valence=0.1, intensity=0.05, agency="other", target="Ann"))
        self.assertGreater(m.state.mood["p"], before)


class Habituation(unittest.TestCase):
    def test_repeated_praise_loses_effect(self):
        m, _ = fresh()
        first = m.feel(PRAISE)["effects"]["effective_intensity"]
        for _ in range(8):
            last = m.feel(PRAISE)["effects"]["effective_intensity"]
        self.assertLess(last, first * 0.5)

    def test_negativity_bias_habituates_less(self):
        m, _ = fresh()
        firsts, lasts = {}, {}
        for name, ap in (("pos", PRAISE), ("neg", INSULT)):
            firsts[name] = m.feel(ap)["effects"]["effective_intensity"]
            for _ in range(8):
                lasts[name] = m.feel(ap)["effects"]["effective_intensity"]
        self.assertGreater(lasts["neg"] / firsts["neg"], lasts["pos"] / firsts["pos"])

    def test_habituation_wears_off(self):
        m, c = fresh()
        first = m.feel(PRAISE)["effects"]["effective_intensity"]
        for _ in range(5):
            m.feel(PRAISE)
        c[0] += 3 * 86400
        again = m.feel(PRAISE)["effects"]["effective_intensity"]
        self.assertGreater(again, first * 0.9)


class Baseline(unittest.TestCase):
    def _days(self, m, c, days, ap, per_day=5):
        for _ in range(days):
            for _ in range(per_day):
                c[0] += 3600
                m.feel(ap)
            c[0] += (24 - per_day) * 3600
            m.consolidate()

    def test_good_weeks_raise_background(self):
        m, c = fresh()
        self._days(m, c, 14, dict(PRAISE, event="success", agency="self", target=None, competence=0.5))
        self.assertGreater(m.state.baseline["p"], 0.1)

    def test_bad_weeks_lower_background_but_bounded(self):
        m, c = fresh()
        self._days(m, c, 21, INSULT, per_day=6)
        self.assertLess(m.state.baseline["p"], -0.1)
        self.assertGreaterEqual(m.state.baseline["p"], m.temperament.baseline_min)

    def test_resilience_recovers_after_bad_period(self):
        m, c = fresh()
        self._days(m, c, 14, INSULT, per_day=6)
        low = m.state.baseline["p"]
        for _ in range(30):  # a month of normal, pleasant contact
            c[0] += 86400
            m.feel(dict(event="chat", valence=0.05, intensity=0.05, agency="other", target="Ann"))
        self.assertGreater(m.state.baseline["p"], low / 2)

    def test_no_spiral_when_idle(self):
        m, c = fresh()
        c[0] += 60 * 86400
        m.tick()
        self.assertGreater(m.state.mood["p"], -0.2)
        self.assertNotIn("baseline_crisis", m.snapshot()["alerts"])


class Drives(unittest.TestCase):
    def test_loneliness_emerges_then_contact_fixes_it(self):
        m, c = fresh(dormant_after_h=0)
        c[0] += 3 * 86400
        m.tick()
        self.assertGreater(m.engine.emotion_level(m.state, "loneliness"), 0.05)
        c[0] += 60
        for _ in range(3):
            m.feel(dict(event="chat", valence=0.1, intensity=0.05, agency="other", target="Ann"))
        self.assertLess(m.state.drives["social"], 0.6)
        c[0] += 6 * 3600
        m.tick()
        self.assertLess(m.engine.emotion_level(m.state, "loneliness"), 0.05)

    def test_dormant_agent_does_not_pine(self):
        m, c = fresh(dormant_after_h=24)
        c[0] += 10 * 86400
        m.tick()
        self.assertLess(m.state.drives["social"], 0.6)

    def test_chronic_need_dulls(self):
        m, c = fresh(dormant_after_h=0, need_adaptation_d=2)
        c[0] += 3 * 86400
        m.tick()
        early = m.engine.emotion_level(m.state, "loneliness")
        c[0] += 20 * 86400
        m.tick()
        late = m.engine.emotion_level(m.state, "loneliness")
        self.assertLessEqual(late, m.temperament.need_feeling_max)
        self.assertGreater(early, 0)
        self.assertLess(late, m.temperament.need_feeling_max * 0.6)


class Relationship(unittest.TestCase):
    def test_loved_ones_words_weigh_more(self):
        m, _ = fresh()
        m.state.relationships["Ann"] = {"affection": 0.9, "trust": 0.5, "resentment": 0, "fear": 0,
                                        "interactions": 50, "first_seen": T0, "last_seen": T0}
        a = m.feel(dict(INSULT, target="Ann"))["effects"]["effective_intensity"]
        b = m.feel(dict(INSULT, event="insult2", target="Stranger"))["effects"]["effective_intensity"]
        self.assertGreater(a, b)

    def test_grudge_fades_bond_stays(self):
        m, c = fresh()
        for _ in range(5):
            m.feel(PRAISE)
            m.feel(dict(INSULT, event="snipe", target="Ann"))
        r0 = dict(m.state.relationships["Ann"])
        c[0] += 14 * 86400
        m.tick()
        r1 = m.state.relationships["Ann"]
        self.assertLess(r1["resentment"], r0["resentment"] * 0.1)
        self.assertGreater(r1["affection"], r0["affection"] * 0.8)


class Memory(unittest.TestCase):
    def test_recall_retriggers_weaker_each_time(self):
        m, c = fresh()
        ev = m.feel(dict(event="loss", valence=-0.8, intensity=0.7, loss=True, target="Friend"))
        c[0] += 7 * 86400
        m.tick()
        r1 = m.recall(ev["id"])["emotions"]["sadness"]
        c[0] += 7 * 86400
        m.tick()
        r2 = m.recall(ev["id"])["emotions"]["sadness"]
        self.assertGreater(r1, 0)
        self.assertLess(r2, r1)

    def test_consolidation_ruminates_strong_negative(self):
        m, c = fresh(rumination=0.8)
        m.feel(dict(INSULT, intensity=0.9, relevance=1.0))
        c[0] += 12 * 3600
        m.tick()
        out = m.consolidate()
        self.assertEqual(out["ruminated"], 1)
        self.assertGreater(m.engine.emotion_level(m.state, "anger"), 0)

    def test_many_small_good_things_make_a_good_background(self):
        m, c = fresh()
        for _ in range(20):
            for _ in range(8):
                c[0] += 1800
                m.feel(dict(event="task_success", valence=0.4, intensity=0.12, agency="self", competence=0.3))
            c[0] += 20 * 3600
            m.consolidate()
        self.assertGreater(m.state.baseline["p"], 0.1)

    def test_day_balance_negative(self):
        m, c = fresh()
        for _ in range(10):
            for _ in range(8):
                c[0] += 1800
                m.feel(dict(event="tool_failure", valence=-0.4, intensity=0.12, agency="circumstance", blocked=True))
            c[0] += 20 * 3600
            out = m.consolidate()
        self.assertLess(out["day_balance"], 0)
        self.assertLess(m.state.baseline["p"], -0.05)

    def test_consolidation_ignores_weak_events(self):
        m, c = fresh()
        m.feel(dict(event="chat", valence=0.05, intensity=0.05))
        c[0] += 3600
        self.assertEqual(m.consolidate()["ruminated"] + m.consolidate()["savored"], 0)


class Outputs(unittest.TestCase):
    def test_label_hysteresis(self):
        m, _ = fresh()
        m.state.mood["p"] = 0.08  # just over the neutral/content border (0.07)
        m.engine._update_label(m.state)
        self.assertEqual(m.snapshot()["label"], "neutral")
        m.state.mood["p"] = 0.11
        m.engine._update_label(m.state)
        self.assertEqual(m.snapshot()["label"], "content")

    def test_prompt_mentions_feeling_and_rules(self):
        m, _ = fresh()
        m.feel(dict(INSULT, intensity=0.9))
        text = m.prompt(lang="en", interlocutor="Bob")
        self.assertIn("anger", text)
        self.assertIn("never stop you from doing a task", text)
        hu = m.prompt(lang="hu", interlocutor="Bob")
        self.assertIn("harag", hu)
        self.assertIn("Kemény szabályok", hu)

    def test_angry_agent_less_patient_and_warm(self):
        m, _ = fresh()
        calm = m.modulators("Bob")
        m.feel(dict(INSULT, intensity=1.0, relevance=1.0))
        angry = m.modulators("Bob")
        self.assertLess(angry["patience"], calm["patience"])
        self.assertLess(angry["warmth"], calm["warmth"])

    def test_state_roundtrip_through_store(self):
        store = Store(":memory:")
        clock = [T0]
        a = Limbic("x", store=store, clock=lambda: clock[0])
        a.feel(PRAISE)
        b = Limbic("x", store=store, clock=lambda: clock[0])
        self.assertEqual(a.state.to_dict(), b.state.to_dict())


class Appraisers(unittest.TestCase):
    def test_keyword_appraiser_hu(self):
        aps = KeywordAppraiser().appraise("Ügyes vagy, köszi!", speaker="Ádám")
        events = {a.event for a in aps}
        self.assertIn("praise", events)
        self.assertTrue(all(a.target == "Ádám" for a in aps if a.agency == "other"))

    def test_keyword_affinity_from_likes(self):
        t = Temperament(likes=["horror"])
        ap = KeywordAppraiser(t).appraise("láttad az új horror filmet?")[0]
        self.assertGreater(ap.affinity, 0)

    def test_parse_appraisals_tolerant(self):
        txt = "```json\n{\"appraisals\":[{\"event\":\"praise\",\"valence\":2,\"bogus\":1}]}\n```"
        aps = parse_appraisals(txt)
        self.assertEqual(aps[0].valence, 1.0)

    def test_llm_appraiser_uses_transport_and_falls_back(self):
        def ok(url, payload, headers):
            self.assertTrue(url.endswith("/chat/completions"))
            return {"choices": [{"message": {"content": json.dumps(
                {"appraisals": [{"event": "praise", "valence": 0.6, "intensity": 0.5, "agency": "other"}]})}}]}
        aps = LLMAppraiser("http://x/v1", "m", transport=ok).appraise("hi", speaker="Ann")
        self.assertEqual(aps[0].target, "Ann")

        def bad(url, payload, headers):
            raise OSError("down")
        llm = LLMAppraiser("http://x/v1", "m", transport=bad)
        aps = llm.appraise("köszi", speaker="Ann")
        self.assertIn("OSError", llm.last_error)
        self.assertEqual(aps[0].event, "thanks")


class Server(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from limbic.server import Hub, make_handler
        from http.server import ThreadingHTTPServer
        cls.hub = Hub(Store(":memory:"))
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(cls.hub, "secret"))
        cls.base = f"http://127.0.0.1:{cls.httpd.server_address[1]}"
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()

    def req(self, method, path, body=None, token="secret"):
        data = json.dumps(body).encode() if body is not None else None
        r = urllib.request.Request(self.base + path, data=data, method=method,
                                   headers={"Content-Type": "application/json",
                                            **({"Authorization": f"Bearer {token}"} if token else {})})
        try:
            with urllib.request.urlopen(r) as resp:
                ct = resp.headers.get("Content-Type", "")
                raw = resp.read().decode()
                return resp.status, (json.loads(raw) if "json" in ct else raw)
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode() or "{}")

    def test_full_flow(self):
        code, _ = self.req("POST", "/api/agents", {"id": "srv", "temperament": {"name": "Srv"}})
        self.assertEqual(code, 201)
        code, out = self.req("POST", "/api/agents/srv/observe", {"text": "szuper, köszi!", "speaker": "Ann"})
        self.assertEqual(code, 200)
        self.assertTrue(out["results"])
        code, st = self.req("GET", "/api/agents/srv/state?interlocutor=Ann")
        self.assertIn("Ann", st["relationships"])
        code, p = self.req("GET", "/api/agents/srv/prompt?lang=hu&interlocutor=Ann")
        self.assertIn("Belső állapot", p["prompt"])
        code, h = self.req("GET", "/api/agents/srv/history?hours=1")
        self.assertGreaterEqual(len(h), 1)
        code, evs = self.req("GET", "/api/agents/srv/events?hours=1")
        self.assertEqual(code, 200)
        code, rc = self.req("POST", f"/api/agents/srv/recall/{evs[0]['id']}")
        self.assertEqual(code, 200)
        code, html = self.req("GET", "/")
        self.assertIn("LIMBIC", html)

    def test_write_requires_token(self):
        code, _ = self.req("POST", "/api/agents", {"id": "nope"}, token=None)
        self.assertEqual(code, 401)

    def test_unknown_agent_404_and_bad_id_400(self):
        self.assertEqual(self.req("GET", "/api/agents/ghost/state")[0], 404)
        self.assertEqual(self.req("POST", "/api/agents", {"id": "bad id!"})[0], 400)


if __name__ == "__main__":
    unittest.main()
