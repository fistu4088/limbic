import json
import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from limbic import installer
from limbic.installer import build_config, default_install_dir, normalize_appraiser_url, write_installation


class InstallerTests(unittest.TestCase):
    def test_generated_launcher_runs_server_in_its_tracked_process(self):
        self.assertIn("from limbic.cli import main", installer.LAUNCHER)
        self.assertNotIn("os.execvpe", installer.LAUNCHER)

    def test_normalize_appraiser_url_accepts_base_or_completion_endpoint(self):
        self.assertEqual(normalize_appraiser_url("https://api.example/v1/"), "https://api.example/v1")
        self.assertEqual(normalize_appraiser_url("https://api.example/v1/chat/completions"),
                         "https://api.example/v1")

    def test_normalize_appraiser_url_rejects_non_http_urls(self):
        for value in ("", "api.example/v1", "file:///tmp/model"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_appraiser_url(value)

    def test_build_config_stores_appraiser_settings(self):
        cfg = build_config("127.0.0.1", 8770, "aria", "https://api.example/v1/chat/completions",
                           "test-model", "test-secret", token="private-token")
        self.assertEqual(cfg["env"]["LIMBIC_LLM_URL"], "https://api.example/v1")
        self.assertEqual(cfg["env"]["LIMBIC_LLM_MODEL"], "test-model")
        self.assertEqual(cfg["env"]["LIMBIC_LLM_KEY"], "test-secret")
        self.assertEqual(cfg["env"]["LIMBIC_TOKEN"], "private-token")

    def test_default_install_dir_is_per_user(self):
        self.assertEqual(default_install_dir("posix", {"XDG_DATA_HOME": "/home/test/data"}),
                         Path("/home/test/data/limbic"))
        self.assertEqual(default_install_dir("nt", {"LOCALAPPDATA": "C:/Users/test/AppData/Local"}),
                         Path("C:/Users/test/AppData/Local/limbic"))

    def test_build_config_generates_token_and_omits_empty_model_settings(self):
        cfg = build_config("127.0.0.1", 8770, "aria", "")
        self.assertEqual(cfg["host"], "127.0.0.1")
        self.assertEqual(cfg["port"], 8770)
        self.assertEqual(cfg["agent_id"], "aria")
        self.assertEqual(len(cfg["env"]["LIMBIC_TOKEN"]), 64)
        self.assertNotIn("LIMBIC_LLM_URL", cfg["env"])

    def test_create_agent_uses_the_installed_token_and_id(self):
        seen = {}

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                seen["path"] = self.path
                seen["auth"] = self.headers.get("Authorization")
                seen["body"] = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                self.send_response(201)
                self.end_headers()
                self.wfile.write(b"{}")

            def log_message(self, format, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            cfg = build_config("127.0.0.1", 8770, "aria", "")
            with patch.dict(os.environ, {
                    "HTTP_PROXY": "http://127.0.0.1:1", "http_proxy": "http://127.0.0.1:1",
                    "NO_PROXY": "", "no_proxy": ""}):
                installer.create_agent(f"http://127.0.0.1:{server.server_port}", cfg)
        finally:
            server.shutdown()
            server.server_close()
        self.assertEqual(seen["path"], "/api/agents")
        self.assertEqual(seen["auth"], f"Bearer {cfg['env']['LIMBIC_TOKEN']}")
        self.assertEqual(seen["body"]["id"], "aria")

    def test_write_installation_copies_source_and_secures_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source"
            (source / "limbic").mkdir(parents=True)
            (source / "limbic" / "__init__.py").write_text("# package")
            (source / "README.md").write_text("limbic")
            target = root / "installed"
            cfg = build_config("127.0.0.1", 8770, "aria", "")
            config_path = write_installation(source, target, cfg)
            self.assertEqual((target / "README.md").read_text(), "limbic")
            self.assertTrue((target / "start-limbic.py").is_file())
            saved = json.loads(config_path.read_text())
            self.assertEqual(saved["env"]["LIMBIC_TOKEN"], cfg["env"]["LIMBIC_TOKEN"])
            if os.name != "nt":
                self.assertEqual(config_path.stat().st_mode & 0o777, 0o600)

    def test_write_installation_refuses_nonempty_existing_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source"
            (source / "limbic").mkdir(parents=True)
            (source / "limbic" / "__init__.py").write_text("# package")
            target = root / "installed"
            target.mkdir()
            marker = target / "keep.txt"
            marker.write_text("do not overwrite")
            cfg = build_config("127.0.0.1", 8770, "aria", "")
            with self.assertRaises(FileExistsError):
                write_installation(source, target, cfg)
            self.assertEqual(marker.read_text(), "do not overwrite")


if __name__ == "__main__":
    unittest.main()
