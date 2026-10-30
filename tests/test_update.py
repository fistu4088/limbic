import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from limbic import update


def git(cwd, *a):
    subprocess.run(["git", "-C", str(cwd), *a], check=True, capture_output=True,
                   env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"})


@unittest.skipUnless(shutil.which("git"), "git not installed")
class SelfUpdate(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.remote = self.tmp / "remote.git"
        git(self.tmp, "init", "-q", "--bare", "-b", "main", str(self.remote))
        self.up = self.tmp / "upstream"
        git(self.tmp, "clone", "-q", str(self.remote), str(self.up))
        (self.up / "a.txt").write_text("1\n")
        git(self.up, "add", ".")
        git(self.up, "commit", "-qm", "first")
        git(self.up, "push", "-q", "origin", "HEAD:main")
        self.local = self.tmp / "local"
        git(self.tmp, "clone", "-q", "-b", "main", str(self.remote), str(self.local))
        self.p = mock.patch.object(update, "REPO", self.local)
        self.p.start()
        update._CACHE.update(t=0.0, data=None)

    def tearDown(self):
        self.p.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def push(self, name, msg):
        (self.up / name).write_text(msg + "\n")
        git(self.up, "add", ".")
        git(self.up, "commit", "-qm", msg)
        git(self.up, "push", "-q", "origin", "HEAD:main")

    def test_up_to_date(self):
        st = update.status(max_age=0)
        self.assertTrue(st["supported"])
        self.assertEqual(st["behind"], 0)
        self.assertFalse(st["can_update"])

    def test_sees_and_applies_new_commits(self):
        self.push("contrib/hermes/x.py".replace("/", "_"), "second")
        os.makedirs(self.up / "contrib" / "hermes", exist_ok=True)
        self.push("contrib/hermes/plugin.py", "plugin fix")
        st = update.status(max_age=0)
        self.assertEqual(st["behind"], 2)
        self.assertEqual([c["subject"] for c in st["incoming"]], ["plugin fix", "second"])
        self.assertTrue(st["integration_changed"])
        self.assertNotIn("plugin_changed", st)
        self.assertTrue(st["can_update"])
        r = update.apply(hub=None, restart=False)
        self.assertTrue(r["updated"])
        self.assertTrue((self.local / "contrib" / "hermes" / "plugin.py").exists())
        self.assertEqual(update.status(max_age=0)["behind"], 0)

    def test_refuses_with_local_edits(self):
        self.push("b.txt", "second")
        (self.local / "a.txt").write_text("edited\n")
        st = update.status(max_age=0)
        self.assertEqual(st["dirty"], ["a.txt"])
        self.assertFalse(st["can_update"])
        with self.assertRaises(RuntimeError):
            update.apply(restart=False)
        self.assertEqual((self.local / "a.txt").read_text(), "edited\n")

    def test_refuses_diverged(self):
        self.push("b.txt", "remote work")
        (self.local / "c.txt").write_text("x\n")
        git(self.local, "add", ".")
        git(self.local, "commit", "-qm", "local work")
        st = update.status(max_age=0)
        self.assertEqual((st["ahead"], st["behind"]), (1, 1))
        with self.assertRaises(RuntimeError):
            update.apply(restart=False)

    def test_disabled(self):
        self.push("b.txt", "second")
        with mock.patch.dict(os.environ, {"LIMBIC_SELF_UPDATE": "0"}):
            self.assertFalse(update.status(max_age=0)["can_update"])
            with self.assertRaises(PermissionError):
                update.apply(restart=False)

    def test_not_a_checkout(self):
        with mock.patch.object(update, "REPO", self.tmp):
            update._CACHE.update(t=0.0, data=None)
            self.assertFalse(update.status(max_age=0)["supported"])


if __name__ == "__main__":
    unittest.main()
