import importlib.util
import json
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path

from limbic import Store
from limbic.server import Hub, make_handler

PLUGIN = Path(__file__).resolve().parent.parent / "contrib" / "hermes" / "plugin" / "__init__.py"
TG = {"chat_id": "100000001", "user_id": "100000001", "chat_type": "dm"}
DC = {"chat_id": "300000000000000003", "user_id": "200000000000000002", "chat_type": "dm"}


def load_plugin():
    spec = importlib.util.spec_from_file_location("limbic_hermes_plugin", PLUGIN)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class FakeManager:
    def __init__(self, gateway=False, tui=False):
        self.has_gateway_message_injector = gateway
        self.has_tui_message_injector = tui


class FakeCtx:
    def __init__(self, gateway=False, tui=False, settings=None):
        self.hooks = {}
        self._manager = FakeManager(gateway, tui)
        self.injected = []
        self.settings = settings or {}

    def register_hook(self, name, cb):
        self.hooks[name] = cb

    def get_config(self, key, default=None):
        return self.settings.get(key, default)

    def inject_message(self, content, role="user", *, session_key=None, origin=None):
        self.injected.append({"content": content, "session_key": session_key, "origin": origin})
        return True


class HermesPlugin(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = load_plugin()
        cls.hub = Hub(Store(":memory:"))
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(cls.hub, "tok"))
        cls.url = f"http://127.0.0.1:{cls.httpd.server_address[1]}"
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        cls.tok = "/tmp/limbic_plugin_test_tok"
        Path(cls.tok).write_text("tok")

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()

    def setUp(self):
        self.hub.drop("aria")
        self.mind = self.hub.get("aria", create=True,
                                 temperament={"name": "Aria", "quiet_hours": [], "dormant_after_h": 0})

    def settings(self, **over):
        s = dict(url=self.url, agent="aria", token_file=self.tok, default_speaker="Ádám", lang="hu",
                 senders={"100000001": "Ádám", "basic:desktop-user": "Ádám"},
                 routes={"Ádám": {"telegram": TG, "discord": DC}}, autonomy_enabled=True)
        s.update(over)
        return s

    def bridge(self, gateway=False, tui=False, **over):
        ctx = FakeCtx(gateway, tui, self.settings(**over))
        b = self.mod.Bridge(lambda: {k: ctx.get_config(k) for k in self.mod.DEFAULTS
                                     if ctx.get_config(k) is not None}, ctx)
        return b, ctx

    def wait_events(self, before, n=1):
        for _ in range(100):
            if self.mind.state.events >= before + n:
                return True
            time.sleep(0.03)
        return False

    def pending_reach_out(self, channel="telegram"):
        m = self.mind
        m.set_relationship("Ádám", reachable=True)
        r = m.state.relationships["Ádám"]
        r.update(affection=0.9, longing=0.9, last_seen=time.time() - 3 * 86400,
                 last_channel=channel, last_channel_t=time.time())
        return m.evaluate_intents()[0]

    def test_default_language_is_english(self):
        ctx = FakeCtx(settings={})
        bridge = self.mod.Bridge(lambda: {}, ctx)
        self.assertEqual(bridge.cfg()["lang"], "en")

    def test_register_wires_hooks_and_starts_thread(self):
        ctx = FakeCtx(settings=self.settings(autonomy_enabled=False))
        b = self.mod.register(ctx)
        self.assertEqual(set(ctx.hooks), {"pre_llm_call", "post_llm_call", "transform_tool_result"})
        self.assertTrue(b._thread.is_alive())
        b._stop.set()

    def test_image_analysis_becomes_an_image_memory(self):
        b, _ = self.bridge()
        b.pre_llm_call(session_id="s9", user_message="nézd", platform="telegram", sender_id="100000001")
        before = self.mind.state.events
        out = b.transform_tool_result(tool_name="terminal", args={"command": 'image-analyze /tmp/x.png "what is it?"'},
                                      result=json.dumps({"output": "A cute kitten on a sofa.", "exit_code": 0}),
                                      session_id="s9", status="ok")
        self.assertIn("A cute kitten on a sofa.", out)
        self.assertIn("saját szemeddel", out)
        self.assertNotIn("exit_code", out)
        self.assertGreater(self.mind.state.events, before)   # felt BEFORE the model sees it
        ev = self.mind.store.events(self.mind.id)[-1]
        self.assertEqual(ev["source"], "image")
        self.assertEqual(ev["speaker"], "Ádám")
        r = self.mind.state.relationships.get("Ádám") or {}
        self.assertEqual(r.get("attraction", 0), 0)   # an image never builds a relationship

    def test_attached_image_is_felt_before_the_reply(self):
        b, _ = self.bridge()
        before = self.mind.state.events
        msg = ("[The user sent an image~ Here's what I can see:\nA cute kitten sleeping in a basket.]\n"
               "[If you need a closer look, use vision_analyze with image_url: /tmp/a.jpg ~]\n\nnézd!")
        out = b.pre_llm_call(session_id="s7", user_message=msg, platform="telegram", sender_id="100000001")
        self.assertGreater(self.mind.state.events, before)            # felt before the model answers
        self.assertIn("A cute kitten sleeping in a basket.", out["context"])
        self.assertIn("saját szemeddel", out["context"])
        self.assertEqual(self.mind.store.events(self.mind.id)[-1]["source"], "image")

    def test_image_note_is_not_appraised_as_the_persons_words(self):
        mod = self.mod
        msg = "[The user attached an image. Here's what it contains:\nA red car.]\n\nTetszik?"
        self.assertEqual(mod.attached_images(msg), ["A red car."])
        self.assertEqual(mod.strip_image_notes(msg), "Tetszik?")
        self.assertEqual(mod.attached_images("[The user sent an image~ Here's what I can see:\nImage analysis failed.]"), [])

    def test_other_terminal_calls_are_ignored(self):
        b, _ = self.bridge()
        before = self.mind.state.events
        self.assertIsNone(b.transform_tool_result(tool_name="terminal", args={"command": "ls"},
                                                  result='{"output":"x","exit_code":0}', session_id="s9", status="ok"))
        self.assertIsNone(b.transform_tool_result(tool_name="terminal", args={"command": "image-analyze /x.png"},
                                                  result='{"output":"boom","exit_code":1}', session_id="s9", status="ok"))
        time.sleep(0.4)
        self.assertEqual(self.mind.state.events, before)

    def test_pre_injects_block_and_tracks_session(self):
        b, _ = self.bridge()
        out = b.pre_llm_call(session_id="s1", user_message="szia", platform="telegram", sender_id="100000001")
        self.assertIn("Belső állapot", out["context"])
        self.assertEqual(b.sessions["s1"], ("Ádám", True, "telegram"))

    def test_post_observes_and_records_channel(self):
        b, _ = self.bridge()
        b.pre_llm_call(session_id="s2", user_message="x", platform="discord", sender_id="100000001")
        before = self.mind.state.events
        b.post_llm_call(session_id="s2", user_message="Ügyes vagy, köszi!", assistant_response="Szívesen!",
                        platform="discord")
        self.assertTrue(self.wait_events(before))
        r = self.mind.state.relationships["Ádám"]
        self.assertEqual(r["last_channel"], "discord")
        self.assertFalse(self.mind.state.thread["private"])  # discord is not in private_platforms

    def test_impulse_turn_is_not_appraised_as_user_words(self):
        b, _ = self.bridge()
        msg = "⟦limbic:reach_out #1⟧ Ez a TE belső késztetésed ..."
        self.assertIsNone(b.pre_llm_call(session_id="s3", user_message=msg, platform="telegram",
                                         sender_id="100000001"))
        before = self.mind.state.events
        b.post_llm_call(session_id="s3", user_message=msg, assistant_response="Szia! Mi újság?",
                        platform="telegram")
        time.sleep(0.4)
        self.assertEqual(self.mind.state.events, before)
        self.assertTrue(self.mind.state.reply_tracking)

    def test_feelings_off(self):
        b, _ = self.bridge(feelings_enabled=False)
        self.assertIsNone(b.pre_llm_call(session_id="s", user_message="x", platform="telegram", sender_id="1"))

    def test_fail_open(self):
        b, _ = self.bridge(url="http://127.0.0.1:9", prompt_timeout_s=0.3)
        self.assertIsNone(b.pre_llm_call(session_id="d", user_message="x", platform="telegram", sender_id="1"))
        self.assertEqual(b.autonomy_tick(), [])

    def test_agent_defaults_to_profile_name(self):
        b, _ = self.bridge(agent="")
        self.assertTrue(b.cfg()["agent"])

    # ---- autonomy ----------------------------------------------------
    def test_gateway_host_delivers_on_last_active_channel(self):
        self.pending_reach_out("telegram")
        b, ctx = self.bridge(gateway=True)
        sent = b.autonomy_tick()
        self.assertEqual(sent[0]["channel"], "telegram")
        inj = ctx.injected[0]
        self.assertEqual(inj["origin"]["chat_id"], "100000001")
        self.assertTrue(inj["content"].startswith("⟦limbic:reach_out"))
        self.assertIn("hiányzik", inj["content"])
        self.assertEqual(self.mind.intents("delivered")[0]["target"], "Ádám")

    def test_discord_route(self):
        self.pending_reach_out("discord")
        b, ctx = self.bridge(gateway=True)
        b.autonomy_tick()
        self.assertEqual(ctx.injected[0]["origin"]["chat_id"], DC["chat_id"])
        self.assertEqual(ctx.injected[0]["origin"]["user_id"], DC["user_id"])

    def test_desktop_host_delivers_into_live_desktop_session(self):
        b, ctx = self.bridge(tui=True)
        b.pre_llm_call(session_id="20261024_1", user_message="hi", platform="desktop", sender_id="basic:desktop-user")
        self.pending_reach_out("desktop")
        b.autonomy_tick()
        self.assertEqual(ctx.injected[0]["session_key"], "20261024_1")

    def test_hosts_dont_steal_before_fallback_then_fallback_works(self):
        it = self.pending_reach_out("desktop")
        gw, gctx = self.bridge(gateway=True)          # desktop app closed: no desktop host
        self.assertEqual(gw.autonomy_tick(), [])       # waits for the desktop host first
        self.mind.state.intents[-1]["created"] -= 6 * 60
        sent = gw.autonomy_tick()
        self.assertEqual(sent[0]["channel"], "telegram")
        self.assertEqual(sent[0]["id"], it["id"])

    def test_desktop_host_ignores_telegram_intent(self):
        self.pending_reach_out("telegram")
        b, ctx = self.bridge(tui=True)
        self.assertEqual(b.autonomy_tick(), [])
        self.assertEqual(ctx.injected, [])

    def test_two_hosts_never_both_send(self):
        self.pending_reach_out("telegram")
        a, actx = self.bridge(gateway=True)
        b, bctx = self.bridge(gateway=True)
        a.autonomy_tick()
        b.autonomy_tick()
        self.assertEqual(len(actx.injected) + len(bctx.injected), 1)

    def test_failed_delivery_is_dismissed_not_ignored(self):
        self.pending_reach_out("telegram")
        b, ctx = self.bridge(gateway=True)
        ctx.inject_message = lambda *a, **k: False
        sent = b.autonomy_tick()
        self.assertFalse(sent[0]["ok"])
        self.assertEqual(self.mind.intents()[-1]["status"], "dismissed")

    def test_autonomy_off_or_no_host(self):
        self.pending_reach_out("telegram")
        b, ctx = self.bridge(gateway=True, autonomy_enabled=False)
        self.assertEqual(b.autonomy_tick(), [])
        b, ctx = self.bridge()  # e.g. a cron/one-shot process: no injector
        self.assertEqual(b.autonomy_tick(), [])
        self.assertEqual(len(self.mind.intents("pending")), 1)


if __name__ == "__main__":
    unittest.main()
