"""Hermes detection, profile -> agent import, plugin setup (synthetic Hermes folders only)."""
import json
import os
import stat
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

from limbic import hermes
from limbic.server import serve


def make_root(tmp: Path) -> Path:
    root = tmp / ".hermes"
    (root / "profiles" / "aria").mkdir(parents=True)
    (root / "config.yaml").write_text("model:\n  default: big-model\n  provider: anthropic\n")
    (root / "SOUL.md").write_text("You are Hermes Agent, a helpful assistant.\n")
    (root / "profiles" / "aria" / "config.yaml").write_text(
        "model:\n  default: small-model\n  base_url: http://127.0.0.1:11434/v1\n  api_key: ${SECRET}\n"
        "plugins:\n  enabled:\n    - limbic\n  entries:\n    limbic:\n      settings:\n        agent: aria2\n")
    (root / "profiles" / "aria" / "SOUL.md").write_text(
        "Te vagy Aria. Nyugodt, száraz humorú segítő, aki nem kér bocsánatot.\n\n"
        "Titkos részlet a második bekezdésben, ami nem kerülhet a leírásba.\n")
    (root / "profiles" / "not a profile").mkdir()
    return root


class Detect(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.root = make_root(self.tmp)

    def test_finds_root_from_env_profile_home(self):
        env = {"HERMES_HOME": str(self.root / "profiles" / "aria"), "HOME": str(self.tmp / "nohome")}
        self.assertEqual(hermes.find_hermes_root(env, use_cli=False), self.root.resolve())

    def test_finds_root_in_home_folder(self):
        self.assertEqual(hermes.find_hermes_root({"HOME": str(self.tmp)}, use_cli=False), self.root.resolve())

    def test_none_without_hermes(self):
        self.assertIsNone(hermes.find_hermes_root({"HOME": str(self.tmp / "empty")}, use_cli=False))

    def test_lists_profiles_with_persona_and_plugin_state(self):
        ps = {p["id"]: p for p in hermes.list_profiles(self.root)}
        self.assertEqual(set(ps), {"default", "aria"})
        self.assertEqual(ps["aria"]["name"], "Aria")
        self.assertEqual(ps["aria"]["lang"], "hu")
        self.assertTrue(ps["aria"]["plugin_enabled"])
        self.assertEqual(ps["aria"]["agent"], "aria2")
        self.assertNotIn("Titkos", ps["aria"]["description"])   # only the first paragraph
        self.assertEqual(ps["default"]["name"], "Hermes")       # "Hermes" is not taken as a persona name
        self.assertFalse(ps["default"]["plugin_enabled"])
        self.assertEqual(ps["default"]["agent"], "default")

    def test_appraiser_offer_skips_oauth_and_env_secrets(self):
        self.assertEqual(hermes.appraiser_from_profile(self.root), {})
        a = hermes.appraiser_from_profile(self.root / "profiles" / "aria")
        self.assertEqual(a, {"url": "http://127.0.0.1:11434/v1", "model": "small-model", "key": ""})

    def test_install_plugin_refuses_foreign_folder(self):
        dest = self.root / "plugins" / "limbic"
        dest.mkdir(parents=True)
        (dest / "plugin.yaml").write_text("name: something-else\n")
        with self.assertRaises(FileExistsError):
            hermes.install_plugin(self.root)

    @unittest.skipIf(os.name == "nt", "shell stub")
    def test_configure_profile_uses_cli_with_profile_home(self):
        calls = self.tmp / "calls.txt"
        stub = self.tmp / "hermes"
        stub.write_text(f"#!{sys.executable}\nimport os,sys\nopen({str(calls)!r},'a').write(os.environ['HERMES_HOME']+'|'+' '.join(sys.argv[1:])+'\\n')\n")
        stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
        ok, log = hermes.configure_profile(self.root, hermes.plugin_settings("http://127.0.0.1:1", "x", "/t", "en"),
                                           cli=str(stub))
        self.assertTrue(ok, log)
        self.assertTrue((self.root / "plugins" / "limbic" / "plugin.yaml").is_file())
        lines = calls.read_text().splitlines()
        self.assertTrue(all(l.startswith(str(self.root) + "|") for l in lines))
        self.assertIn("plugins enable limbic", lines[-1])
        self.assertTrue(any("plugins.entries.limbic.settings.agent x" in l for l in lines))

    def test_without_cli_returns_manual_commands(self):
        with mock.patch.object(hermes, "hermes_cli", return_value=None):
            ok, log = hermes.configure_profile(self.root, {"agent": "x"})
        self.assertFalse(ok)
        self.assertTrue(any("plugins enable limbic" in l for l in log))


class Endpoints(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.root = make_root(self.tmp)
        self.env = mock.patch.dict(os.environ, {"LIMBIC_HERMES_HOME": str(self.root)})
        self.env.start()
        self.httpd = serve(str(self.tmp / "l.db"), "127.0.0.1", 0, token="tok", autonomy_interval=0)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.hub.stop()
        self.env.stop()

    def call(self, method, path, body=None, token="tok"):
        req = urllib.request.Request(self.base + path, method=method,
                                     data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Content-Type": "application/json",
                                              **({"Authorization": "Bearer " + token} if token else {})})
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read())

    def test_token_file_written_private(self):
        f = Path(self.httpd.hub.token_file)
        self.assertEqual(f.read_text().strip(), "tok")
        if os.name != "nt":
            self.assertEqual(f.stat().st_mode & 0o777, 0o600)

    def test_profile_list_needs_token_even_for_get(self):
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self.call("GET", "/api/hermes", token=None)
        self.assertEqual(cm.exception.code, 401)
        code, d = self.call("GET", "/api/hermes")
        self.assertTrue(d["found"])
        self.assertEqual({p["id"] for p in d["profiles"]}, {"default", "aria"})
        self.assertTrue(all("home" not in p for p in d["profiles"]))   # no local paths in the API

    def test_connect_creates_agent_from_persona(self):
        code, d = self.call("POST", "/api/hermes/profiles/aria/connect", {"plugin": False})
        self.assertEqual((code, d["agent"], d["created"]), (201, "aria2", True))
        _, t = self.call("GET", "/api/agents/aria2/temperament")
        self.assertEqual(t["name"], "Aria")
        self.assertIn("Nyugodt", t["description"])
        code, d = self.call("POST", "/api/hermes/profiles/aria/connect", {"plugin": False, "name": "Other"})
        self.assertEqual((code, d["created"]), (200, False))
        _, t = self.call("GET", "/api/agents/aria2/temperament")
        self.assertEqual(t["name"], "Aria")                              # an existing agent is never overwritten

    def test_never_repoints_a_profile_using_another_server_without_force(self):
        cfg = self.root / "profiles" / "aria" / "config.yaml"
        cfg.write_text(cfg.read_text() + "        url: http://10.0.0.9:8770\n")
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self.call("POST", "/api/hermes/profiles/aria/connect", {})
        self.assertEqual(cm.exception.code, 409)
        self.assertIn("10.0.0.9", cfg.read_text())
        self.assertFalse((self.root / "profiles" / "aria" / "plugins").exists())

    def test_disconnect_skips_profile_without_plugin(self):
        code, d = self.call("POST", "/api/hermes/profiles/default/disconnect", {})
        self.assertEqual((code, d["plugin"]), (200, False))

    @unittest.skipIf(os.name == "nt", "shell stub")
    def test_disconnect_disables_plugin_via_cli(self):
        calls = self.tmp / "calls.txt"
        stub = self.tmp / "hermes"
        stub.write_text(f"#!{sys.executable}\nimport os,sys\nopen({str(calls)!r},'a').write(os.environ['HERMES_HOME']+'|'+' '.join(sys.argv[1:])+'\\n')\n")
        stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
        with mock.patch.object(hermes, "hermes_cli", return_value=str(stub)):
            code, d = self.call("POST", "/api/hermes/profiles/aria/disconnect", {})
        self.assertEqual((code, d["plugin"]), (200, False))
        self.assertIn("plugins disable limbic", calls.read_text())
        self.assertTrue(list((self.root / "profiles" / "aria").glob("config.yaml.bak-limbic-*")))

    def test_unknown_profile_404(self):
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self.call("POST", "/api/hermes/profiles/nope/connect", {})
        self.assertEqual(cm.exception.code, 404)

    def test_plugin_creates_its_agent_on_first_contact(self):
        import importlib.util
        src = Path(__file__).resolve().parent.parent / "contrib" / "hermes" / "plugin" / "__init__.py"
        spec = importlib.util.spec_from_file_location("limbic_plugin_autocreate", src)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        tokf = self.tmp / "t"
        tokf.write_text("tok")
        bridge = mod.Bridge(lambda: {"url": self.base, "agent": "fresh", "token_file": str(tokf)})
        out = bridge.pre_llm_call(session_id="s1", user_message="hello", platform="cli")
        self.assertIsNotNone(out)
        code, a = self.call("GET", "/api/agents/fresh/temperament")
        self.assertEqual(a["name"], "Fresh")


if __name__ == "__main__":
    unittest.main()
