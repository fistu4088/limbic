import io
import json
import threading
import time
import unittest
from contextlib import redirect_stdout
from http.server import ThreadingHTTPServer

from limbic import Appraisal, Limbic, Store
from limbic.intents import in_quiet_hours

T0 = 1_700_000_000.0
H = 3600.0


def fresh(**temp):
    temp.setdefault("quiet_hours", [])
    clock = [T0]
    m = Limbic("n", store=Store(":memory:"), temperament=temp, clock=lambda: clock[0])
    return m, clock


def bond(m, name="Ann", affection=0.8, **kw):
    m.set_relationship(name, reachable=kw.pop("reachable", True), **kw)
    r = m.state.relationships[name]
    r["affection"] = affection
    r["last_seen"] = m.state.t
    return r


CHAT = dict(event="chat", valence=0.05, intensity=0.05, agency="other")


class Attachment(unittest.TestCase):
    def test_longing_grows_with_absence_and_affection(self):
        m, c = fresh(dormant_after_h=0)
        bond(m, "Ann", 0.9)
        bond(m, "Bob", 0.0)
        c[0] += 2 * 86400
        m.tick()
        self.assertGreater(m.state.relationships["Ann"]["longing"], 0.5)
        self.assertEqual(m.state.relationships["Bob"]["longing"], 0.0)
        self.assertGreater(m.engine.emotion_level(m.state, "longing", "Ann"), 0)

    def test_no_longing_within_delay(self):
        m, c = fresh()
        bond(m, "Ann", 0.9)
        c[0] += 4 * H
        m.tick()
        self.assertEqual(m.state.relationships["Ann"]["longing"], 0.0)

    def test_reunion_brings_joy_and_clears_longing(self):
        m, c = fresh(dormant_after_h=0)
        bond(m, "Ann", 0.9)
        c[0] += 2 * 86400
        r = m.feel(dict(CHAT, target="Ann"), speaker="Ann")
        self.assertIn("reunion", r["effects"])
        self.assertGreater(r["effects"]["emotions"]["joy"], 0.1)
        self.assertLess(m.state.relationships["Ann"]["longing"], 0.2)

    def test_unanswered_outreach_makes_it_sulk_on_return(self):
        m, c = fresh(dormant_after_h=0)
        bond(m, "Ann", 0.9)
        c[0] += 2 * 86400
        m.state.relationships["Ann"]["unanswered"] = 2
        warm_before = m.modulators("Ann")["warmth"]
        r = m.feel(dict(CHAT, target="Ann"), speaker="Ann")
        self.assertIn("sulk", r["effects"])
        self.assertGreater(m.state.relationships["Ann"]["resentment"], 0)
        self.assertEqual(m.state.relationships["Ann"]["unanswered"], 0)
        self.assertIn("sadness@Ann", m.state.emotions)


class OnlyPeopleAreMissed(unittest.TestCase):
    def test_things_are_never_missed(self):
        m, c = fresh(dormant_after_h=0)
        for _ in range(30):
            m.feel(dict(event="task_success", valence=0.5, intensity=0.3, agency="self", target="SillyTavern"))
        self.assertFalse(m.state.relationships["SillyTavern"]["is_person"])
        c[0] += 5 * 86400
        m.tick()
        self.assertEqual(m.state.relationships["SillyTavern"]["longing"], 0.0)
        self.assertNotIn("longing@SillyTavern", m.state.emotions)

    def test_speaker_becomes_person(self):
        m, c = fresh()
        m.feel(dict(CHAT, target="Bob"), speaker="Bob")
        self.assertTrue(m.state.relationships["Bob"]["is_person"])

    def test_at_most_three_people_missed_at_once(self):
        m, c = fresh(dormant_after_h=0)
        for k in range(6):
            bond(m, f"P{k}", 0.9)
        c[0] += 4 * 86400
        m.tick()
        felt = [k for k in m.state.emotions if k.startswith("longing@")]
        self.assertLessEqual(len(felt), 3)

    def test_fulfilment_without_romance_is_ignored(self):
        m, c = fresh(adult_enabled=True, romance_enabled=True)
        bond(m, "Ann", 0.7, consent={"romance": True, "adult": True})
        r = m.feel(dict(event="task_completed", valence=0.6, intensity=0.4, agency="other", target="Ann",
                        fulfilment=0.9), speaker="Ann")
        self.assertNotIn("tenderness", r["effects"]["emotions"])
        self.assertEqual(m.state.relationships["Ann"]["intimacy"], 0.0)


class Recognition(unittest.TestCase):
    def test_praise_feeds_recognition(self):
        m, c = fresh(dormant_after_h=0)
        c[0] += 5 * 86400
        m.tick()
        before = m.state.drives["recognition"]
        m.feel(dict(event="praise", valence=0.7, intensity=0.6, agency="other", target="Ann"))
        self.assertLess(m.state.drives["recognition"], before - 0.2)


class ReachOut(unittest.TestCase):
    def setUp(self):
        self.m, self.c = fresh(dormant_after_h=0)
        bond(self.m, "Ann", 0.9, contact="telegram:1")
        self.c[0] += 2 * 86400

    def test_reach_out_when_missing_reachable_person(self):
        new = self.m.evaluate_intents()
        self.assertEqual([(i["kind"], i["target"]) for i in new], [("reach_out", "Ann")])
        self.assertEqual(new[0]["contact"], "telegram:1")
        self.assertEqual(self.m.evaluate_intents(), [])  # no duplicates while open

    def test_not_reachable_means_no_outreach(self):
        self.m.set_relationship("Ann", reachable=False)
        self.assertEqual(self.m.evaluate_intents(), [])

    def test_autonomy_off(self):
        self.m.set_temperament({"autonomy_enabled": False})
        self.assertEqual(self.m.evaluate_intents(), [])

    def test_quiet_hours_block(self):
        self.m.set_temperament({"quiet_hours": [0, 24]})
        self.assertEqual(self.m.evaluate_intents(), [])

    def test_quiet_hours_wraparound(self):
        class T:
            quiet_hours = [23, 8]
        lt = time.localtime(T0)
        at = lambda hr: time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, hr, 30, 0, 0, 0, -1))  # noqa: E731
        self.assertTrue(in_quiet_hours(T, at(23)))
        self.assertTrue(in_quiet_hours(T, at(3)))
        self.assertFalse(in_quiet_hours(T, at(12)))

    def test_ignored_outreach_backs_off_then_stops(self):
        m, c = self.m, self.c
        T = m.temperament
        attempts = 0
        for _ in range(40):
            new = m.evaluate_intents()
            for it in new:
                m.claim(it["id"])
                attempts += 1
            c[0] += 6 * H
        self.assertEqual(attempts, T.max_unanswered)
        self.assertEqual(m.state.relationships["Ann"]["unanswered"], T.max_unanswered)
        gaps = [e["t"] for e in m.store.events("n", kind="outreach")][::-1]
        self.assertGreater(gaps[2] - gaps[1], gaps[1] - gaps[0])  # exponential back-off

    def test_answer_closes_intent(self):
        it = self.m.evaluate_intents()[0]
        self.m.claim(it["id"])
        self.c[0] += H
        self.m.feel(dict(CHAT, target="Ann"), speaker="Ann")
        self.assertEqual(self.m.intents()[0]["status"], "answered")
        self.assertEqual(self.m.state.relationships["Ann"]["unanswered"], 0)

    def test_pending_expires(self):
        self.m.evaluate_intents()
        self.c[0] += 3 * H
        self.m.evaluate_intents()
        self.assertEqual(self.m.intents()[0]["status"], "expired")

    def test_daily_cap(self):
        self.m.set_temperament({"max_outreach_per_day": 1, "outreach_cooldown_h": 0})
        bond(self.m, "Bob", 0.9)
        self.m.state.relationships["Bob"]["last_seen"] -= 3 * 86400
        self.m.state.relationships["Bob"]["longing"] = 0.9
        first = self.m.evaluate_intents()
        self.m.claim(first[0]["id"])
        self.assertEqual(self.m.evaluate_intents(), [])

    def test_instruction_has_reason_and_no_guilt_rule(self):
        it = self.m.evaluate_intents()[0]
        hu = self.m.instruction(it["id"], "hu")
        self.assertIn("hiányzik", hu)
        self.assertIn("Kemény szabályok", hu)
        en = self.m.instruction(it["id"], "en")
        self.assertIn("miss them", en)


class Thread(unittest.TestCase):
    def liked(self, m, n=4, topic="horror films"):
        for _ in range(n):
            m.feel(dict(event="topic", valence=0.3, intensity=0.3, affinity=0.9, novelty=0.7,
                        agency="other", target="Ann", topic=topic), speaker="Ann")

    def test_liked_topic_builds_engagement_and_excitement(self):
        m, c = fresh()
        bond(m, "Ann", 0.5)
        self.liked(m)
        self.assertGreater(m.state.thread["engagement"], 0.5)
        self.assertGreater(m.engine.emotion_level(m.state, "excitement"), 0.1)
        st = m.snapshot("Ann")["steering"]
        self.assertTrue(any(s["direction"] == "topic" and s["topic"] == "horror films" for s in st))

    def test_disliked_topic_kills_engagement(self):
        m, c = fresh()
        bond(m, "Ann", 0.5)
        self.liked(m)
        e0 = m.state.thread["engagement"]
        m.feel(dict(event="topic", valence=0.1, intensity=0.2, affinity=-0.9, agency="other",
                    target="Ann"), speaker="Ann")
        self.assertLess(m.state.thread["engagement"], e0)

    def test_check_in_after_engaging_chat_goes_quiet(self):
        m, c = fresh()
        bond(m, "Ann", 0.5)
        self.liked(m)
        m.note_reply()
        c[0] += 10 * 60
        self.assertEqual(m.evaluate_intents(), [])
        c[0] += 15 * 60
        new = m.evaluate_intents()
        self.assertEqual(new[0]["kind"], "check_in")
        self.assertEqual(new[0]["topic"], "horror films")
        self.assertIn("horror films", m.instruction(new[0]["id"], "en"))
        c[0] += 10 * 60
        self.assertEqual([i for i in m.evaluate_intents() if i["kind"] == "check_in"], [])

    def test_no_check_in_when_agent_owes_the_reply(self):
        m, c = fresh()
        bond(m, "Ann", 0.5)
        m.note_reply()
        c[0] += 1
        self.liked(m)  # user spoke last, agent never answered
        c[0] += 30 * 60
        self.assertEqual(m.evaluate_intents(), [])

    def test_no_check_in_for_boring_chat(self):
        m, c = fresh()
        bond(m, "Ann", 0.5)
        for _ in range(4):
            m.feel(dict(CHAT, target="Ann"), speaker="Ann")
        c[0] += 30 * 60
        self.assertEqual(m.evaluate_intents(), [])


FLIRT = dict(event="flirt", valence=0.4, intensity=0.35, agency="other", target="Ann", erotic=0.8, romance=0.3)


class Desire(unittest.TestCase):
    def adult(self, consent=True, enabled=True):
        m, c = fresh(adult_enabled=enabled, romance_enabled=enabled, dormant_after_h=0)
        bond(m, "Ann", 0.7, consent={"romance": consent, "adult": consent})
        return m, c

    def test_disabled_by_default(self):
        m, c = fresh()
        bond(m, "Ann", 0.7, consent={"romance": True, "adult": True})
        m.feel(FLIRT, speaker="Ann", private=True)
        self.assertEqual(m.engine.emotion_level(m.state, "desire"), 0.0)
        self.assertEqual(m.state.drives["libido"], 0.0)

    def test_no_consent_no_desire(self):
        m, c = self.adult(consent=False)
        m.feel(FLIRT, speaker="Ann", private=True)
        self.assertEqual(m.engine.emotion_level(m.state, "desire"), 0.0)
        self.assertFalse(any(s["direction"] in ("adult", "romance") for s in m.snapshot("Ann")["steering"]))

    def test_appraiser_cannot_grant_consent(self):
        ap = Appraisal.from_dict({**FLIRT, "consent": {"adult": True}})
        self.assertFalse(hasattr(ap, "consent"))

    def test_desire_builds_and_steers_only_in_private(self):
        m, c = self.adult()
        c[0] += 86400
        for _ in range(4):
            m.feel(FLIRT, speaker="Ann", private=False)
        self.assertGreater(m.engine.emotion_level(m.state, "desire", "Ann"), m.temperament.steer_threshold)
        self.assertFalse(any(s["direction"] == "adult" for s in m.snapshot("Ann")["steering"]))
        m.feel(FLIRT, speaker="Ann", private=True)
        self.assertTrue(any(s["direction"] == "adult" for s in m.snapshot("Ann")["steering"]))
        self.assertIn("ebbe az irányba", m.prompt("hu", "Ann"))

    def test_rebuff_drops_desire_and_blocks_steering(self):
        m, c = self.adult()
        for _ in range(5):
            m.feel(FLIRT, speaker="Ann", private=True)
        d0 = m.engine.emotion_level(m.state, "desire", "Ann")
        r = m.feel(dict(event="rebuff", valence=-0.2, intensity=0.25, agency="other", target="Ann", rebuff=True),
                   speaker="Ann", private=True)
        self.assertTrue(r["effects"]["rebuff"])
        self.assertLess(m.engine.emotion_level(m.state, "desire", "Ann"), d0 * 0.5)
        m.feel(FLIRT, speaker="Ann", private=True)  # within cooldown: no new desire
        self.assertFalse(any(s["direction"] in ("adult", "romance") for s in m.snapshot("Ann")["steering"]))
        self.assertEqual(m.state.relationships["Ann"]["resentment"], 0.0)

    def test_words_satisfy_libido_and_fulfilment_gives_afterglow(self):
        m, c = self.adult()
        c[0] += 3 * 86400
        m.tick()
        lib0 = m.state.drives["libido"]
        self.assertGreater(lib0, 0.5)
        m.feel(FLIRT, speaker="Ann", private=True)
        self.assertLess(m.state.drives["libido"], lib0)
        r = m.feel(dict(FLIRT, event="intimate", fulfilment=1.0), speaker="Ann", private=True)
        self.assertLess(m.state.drives["libido"], 0.2)
        self.assertIn("tenderness", r["effects"]["emotions"])
        self.assertGreater(m.state.relationships["Ann"]["intimacy"], 0)

    def test_intimacy_need_alone_does_not_target_a_stranger(self):
        m, c = self.adult()
        m.state.relationships["Ann"]["affection"] = 0.0
        c[0] += 5 * 86400
        m.tick()
        m.feel(dict(event="chat", valence=0.05, intensity=0.05, agency="other", target="Ann"),
               speaker="Ann", private=True)
        self.assertGreater(m.state.drives["intimacy"], 0.6)
        self.assertFalse(any(s["direction"] == "romance" for s in m.snapshot("Ann")["steering"]))

    def test_romance_tenderness_and_prompt(self):
        m, c = self.adult()
        for _ in range(4):
            m.feel(dict(event="affection", valence=0.5, intensity=0.4, agency="other", target="Ann", romance=0.9),
                   speaker="Ann")
        self.assertGreater(m.engine.emotion_level(m.state, "tenderness", "Ann"), 0.3)
        self.assertNotIn("romantic", m.prompt("en", "Ann"))  # not private: no romance steering
        m.feel(dict(event="affection", valence=0.5, intensity=0.4, agency="other", target="Ann", romance=0.9),
               speaker="Ann", private=True)
        self.assertIn("romantic", m.prompt("en", "Ann"))


class MoodBounds(unittest.TestCase):
    def test_burst_of_events_never_overshoots_target(self):
        m, c = fresh()
        bond(m, "Ann", 0.5)
        for k in range(12):
            c[0] += 60
            m.feel(dict(event="topic", valence=0.4, intensity=0.4, affinity=0.9, humor=0.5,
                        agency="other", target="Ann", topic="x"), speaker="Ann")
            tgt = m.engine.mood_target(m.state)
            self.assertLessEqual(m.state.mood["p"], max(tgt["p"], 0) + 1e-9)
        self.assertNotEqual(m.snapshot()["label"], "elated")  # 12 minutes of fun is not ecstasy


class ServerAutonomy(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from limbic.server import Hub, make_handler
        cls.posted = []
        cls.hub = Hub(Store(":memory:"), poster=lambda url, payload: cls.posted.append((url, payload)))
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(cls.hub, "tok"))
        cls.base = f"http://127.0.0.1:{cls.httpd.server_address[1]}"
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()

    def req(self, method, path, body=None):
        import urllib.request
        data = json.dumps(body).encode() if body is not None else (b"{}" if method != "GET" else None)
        r = urllib.request.Request(self.base + path, data=data, method=method,
                                   headers={"Content-Type": "application/json", "Authorization": "Bearer tok"})
        with urllib.request.urlopen(r) as resp:
            raw = resp.read().decode()
            return json.loads(raw) if "json" in resp.headers.get("Content-Type", "") else raw

    def test_relationship_unicode_and_outreach_cli(self):
        from limbic.cli import main
        self.req("POST", "/api/agents", {"id": "aria", "temperament": {
            "quiet_hours": [], "dormant_after_h": 0, "webhook_url": "http://hook"}})
        r = self.req("PUT", "/api/agents/aria/relationships/%C3%81d%C3%A1m",
                     {"reachable": True, "contact": "telegram:42", "consent": {"adult": True}})
        self.assertTrue(r["reachable"])
        self.assertTrue(r["consent"]["adult"])
        tok = "/tmp/limbic_test_tok"
        open(tok, "w").write("tok")
        out = io.StringIO()
        with redirect_stdout(out):
            main(["outreach", "aria", "--url", self.base, "--token-file", tok])
        self.assertIn('"wakeAgent": false', out.getvalue())

        mind = self.hub.get("aria")
        mind.state.relationships["Ádám"].update(affection=0.9, longing=0.9)
        made = self.req("POST", "/api/autonomy/run")
        self.assertEqual(made[0]["kind"], "reach_out")
        time.sleep(0.2)
        self.assertEqual(self.posted[-1][0], "http://hook")
        self.assertIn("instruction_hu", self.posted[-1][1])

        out = io.StringIO()
        with redirect_stdout(out):
            main(["outreach", "aria", "--url", self.base, "--token-file", tok, "--lang", "hu"])
        text = out.getvalue()
        self.assertIn("reach_out -> Ádám via telegram:42", text)
        self.assertIn("hiányzik", text)
        self.assertEqual(self.req("GET", "/api/agents/aria/intents?status=delivered")[0]["target"], "Ádám")


if __name__ == "__main__":
    unittest.main()
