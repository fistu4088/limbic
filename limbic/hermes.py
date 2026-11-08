"""Find a local Hermes Agent installation and connect its profiles to limbic.

Standard library only. Nothing here sends data anywhere: it reads the Hermes
folder on this computer, copies the bundled plugin into a profile and writes
the plugin settings through the ``hermes`` command line (the same thing a user
would type by hand).

Used by the installer (``python -m limbic.installer``) and by the dashboard
("Hermes" button -> add a profile as a limbic agent).
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

PLUGIN_SRC = Path(__file__).resolve().parent.parent / "contrib" / "hermes" / "plugin"
ID_RE = re.compile(r"[\w.-]{1,64}")
_NAME_PATTERNS = (
    re.compile(r"^\s*(?:you are|your name is|i am|i'm)\s+([A-ZÁÉÍÓÖŐÚÜŰ][\w'-]{1,30})", re.I | re.M),
    re.compile(r"^\s*(?:te vagy|a neved|az én nevem)\s+([A-ZÁÉÍÓÖŐÚÜŰ][\w'-]{1,30})", re.I | re.M),
    re.compile(r"^\s*#\s+([A-ZÁÉÍÓÖŐÚÜŰ][\w'-]{1,30})\s*$", re.M),
)
_STOP_NAMES = {"a", "an", "the", "az", "egy", "here", "now", "claude", "hermes", "an", "assistant", "agent"}


# ------------------------------------------------------------------ discovery
def _run(cmd: list[str], home: Path | None = None, timeout: float = 60) -> tuple[int, str]:
    env = dict(os.environ)
    if home is not None:
        env["HERMES_HOME"] = str(home)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    try:
        p = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=timeout,
                           encoding="utf-8", errors="replace")
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except (OSError, subprocess.SubprocessError) as exc:
        return 1, str(exc)


def hermes_cli() -> str | None:
    """Path of the ``hermes`` command, if it is installed."""
    found = shutil.which("hermes")
    if found:
        return found
    for cand in (Path.home() / ".local" / "bin" / "hermes", Path.home() / ".hermes" / "bin" / "hermes"):
        if cand.is_file():
            return str(cand)
    return None


def _root_of(path: Path) -> Path:
    """A profile home lives in <root>/profiles/<name>; the root is the default profile."""
    return path.parent.parent if path.parent.name == "profiles" else path


def _looks_like_home(path: Path) -> bool:
    return path.is_dir() and ((path / "config.yaml").is_file() or (path / "profiles").is_dir())


def find_hermes_root(environ: dict | None = None, use_cli: bool = True) -> Path | None:
    """The Hermes root folder (the default profile's home), or None.

    Order: LIMBIC_HERMES_HOME, HERMES_HOME, ``hermes config path``, then the usual
    per-user folders on Linux/macOS/Windows.
    """
    env = environ if environ is not None else os.environ
    cands: list[Path] = []
    for key in ("LIMBIC_HERMES_HOME", "HERMES_HOME"):
        if env.get(key):
            cands.append(_root_of(Path(env[key]).expanduser()))
    cli = hermes_cli() if use_cli else None
    if cli:
        rc, out = _run([cli, "config", "path"], timeout=30)
        line = next((l.strip() for l in out.splitlines() if l.strip().endswith("config.yaml")), "")
        if rc == 0 and line:
            cands.append(_root_of(Path(line).expanduser().parent))
    home = Path(env.get("USERPROFILE") or env.get("HOME") or Path.home())
    cands.append(home / ".hermes")
    for key in ("LOCALAPPDATA", "APPDATA"):
        if env.get(key):
            cands.append(Path(env[key]) / "hermes")
    for c in cands:
        if _looks_like_home(c):
            return c.resolve()
    return None


def _yaml_scalar(text: str, block: str, key: str) -> str:
    """Tiny reader for `block:\\n  key: value` in config.yaml (no PyYAML needed)."""
    m = re.search(rf"(?m)^{re.escape(block)}:\s*\n((?:[ \t]+.*\n?|\s*\n)*)", text)
    if not m:
        return ""
    k = re.search(rf"(?m)^[ \t]+{re.escape(key)}:[ \t]*(.+?)\s*$", m.group(1))
    return k.group(1).strip().strip("'\"") if k else ""


def _plugin_enabled(text: str) -> bool:
    m = re.search(r"(?m)^plugins:\s*\n((?:[ \t]+.*\n?|\s*\n)*)", text)
    if not m:
        return False
    en = re.search(r"(?m)^([ \t]+)enabled:[ \t]*(\[.*\])?\s*\n((?:\1[ \t]*-.*\n?)*)", m.group(1))
    if not en:
        return False
    return "limbic" in (en.group(2) or "") or bool(re.search(r"-\s*['\"]?limbic['\"]?\s*$", en.group(3), re.M))


def _limbic_setting(text: str, key: str) -> str:
    m = re.search(r"(?ms)^[ \t]+limbic:\s*\n.*?^[ \t]+settings:\s*\n((?:[ \t]{6,}.*\n?)*)", text)
    if not m:
        return ""
    k = re.search(rf"(?m)^[ \t]+{re.escape(key)}:[ \t]*(.+?)\s*$", m.group(1))
    return k.group(1).strip().strip("'\"") if k else ""


def guess_lang(text: str) -> str:
    words = len(re.findall(r"\b(?:vagy|nem|hogy|és|egy|vagyok|neked|aki|az|te)\b", text, re.I))
    marks = len(re.findall(r"[áéíóöőúüűÁÉÍÓÖŐÚÜŰ]", text))
    return "hu" if words >= 3 or re.search(r"[őűŐŰ]", text) or marks >= 6 else "en"


def persona_from_soul(text: str, fallback: str) -> tuple[str, str]:
    """(display name, short description) from a SOUL.md, without the private details.

    Only the first paragraph is used as the description and it is capped, so the
    temperament never carries the whole persona prompt.
    """
    name = ""
    for pat in _NAME_PATTERNS:
        for m in pat.finditer(text or ""):
            cand = m.group(1).strip(".,:;!'\"")
            if cand.lower() not in _STOP_NAMES:
                name = cand
                break
        if name:
            break
    paras = [p.strip() for p in re.split(r"\n\s*\n", text or "") if p.strip() and not p.strip().startswith("#")]
    desc = re.sub(r"\s+", " ", paras[0]) if paras else ""
    if len(desc) > 240:
        desc = desc[:237].rsplit(" ", 1)[0] + "…"
    return name or fallback, desc


def list_profiles(root: Path | None) -> list[dict]:
    """Every Hermes profile under root: id, home, name, description, lang, plugin state."""
    if root is None:
        return []
    homes: list[tuple[str, Path]] = []
    if (root / "config.yaml").is_file():
        homes.append(("default", root))
    pdir = root / "profiles"
    if pdir.is_dir():
        for p in sorted(pdir.iterdir()):
            if p.is_dir() and (p / "config.yaml").is_file() and ID_RE.fullmatch(p.name):
                homes.append((p.name, p))
    out = []
    for pid, home in homes:
        try:
            cfg = (home / "config.yaml").read_text(encoding="utf-8", errors="replace")
        except OSError:
            cfg = ""
        try:
            soul = (home / "SOUL.md").read_text(encoding="utf-8", errors="replace")
        except OSError:
            soul = ""
        name, desc = persona_from_soul(soul, pid.replace("-", " ").title() if pid != "default" else "Hermes")
        out.append({
            "id": pid, "home": str(home), "name": name, "description": desc,
            "lang": guess_lang(soul), "model": _yaml_scalar(cfg, "model", "default"),
            "plugin_installed": (home / "plugins" / "limbic" / "plugin.yaml").is_file(),
            "plugin_enabled": _plugin_enabled(cfg),
            "agent": _limbic_setting(cfg, "agent") or pid,
            "url": _limbic_setting(cfg, "url"),
        })
    return out


def appraiser_from_profile(home: Path) -> dict:
    """The profile's own OpenAI-compatible model endpoint, if it has one in config.yaml.

    Returns {} for OAuth/vendor providers (no base_url). The key is only taken from
    config.yaml's model.api_key, never from .env or credential stores.
    """
    try:
        cfg = (Path(home) / "config.yaml").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    url = _yaml_scalar(cfg, "model", "base_url")
    if not url.startswith(("http://", "https://")):
        return {}
    key = _yaml_scalar(cfg, "model", "api_key")
    if key.startswith("$"):
        key = ""   # an env reference; limbic does not read Hermes's secrets
    return {"url": url, "model": _yaml_scalar(cfg, "model", "default"), "key": key}


# ------------------------------------------------------------------ connecting
def install_plugin(home: Path, src: Path = PLUGIN_SRC) -> Path:
    """Copy the bundled plugin to <home>/plugins/limbic (replacing an older limbic copy only)."""
    dest = Path(home) / "plugins" / "limbic"
    if dest.is_symlink():
        return dest   # an operator-managed link to a shared copy; leave it alone
    if dest.exists():
        manifest = dest / "plugin.yaml"
        if not manifest.is_file() or not re.search(r"(?m)^name:\s*limbic\s*$", manifest.read_text(encoding="utf-8", errors="replace")):
            raise FileExistsError(f"{dest} exists and is not the limbic plugin")
        shutil.rmtree(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, dest, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    return dest


def plugin_settings(url: str, agent: str, token_file: str, lang: str) -> dict:
    return {"url": url, "agent": agent, "token_file": token_file, "lang": lang}


def configure_profile(home: Path, settings: dict, cli: str | None = None,
                      enable: bool = True) -> tuple[bool, list[str]]:
    """Install the plugin into one profile and write its settings via the hermes CLI.

    Returns (ok, log lines). Without the CLI it returns the commands to run by hand.
    """
    home = Path(home)
    cfg = home / "config.yaml"
    log = []
    if cfg.is_file():
        import time
        bak = home / f"config.yaml.bak-limbic-{time.strftime('%Y%m%d-%H%M%S')}"
        shutil.copy2(cfg, bak)
        log.append(f"backup -> {bak}")
    log.append(f"plugin -> {install_plugin(home)}")
    cmds = [["config", "set", f"plugins.entries.limbic.settings.{k}", str(v)] for k, v in settings.items()]
    cmds.append(["config", "set", "plugins.entries.limbic.allow_gateway_injection", "true"])
    if enable:
        cmds.append(["plugins", "enable", "limbic"])
    cli = cli or hermes_cli()
    if not cli:
        log.append("hermes command not found; run these with HERMES_HOME=" + str(home) + ":")
        log += ["  hermes " + " ".join(f"'{a}'" if " " in a else a for a in c) for c in cmds]
        return False, log
    ok = True
    for c in cmds:
        rc, out = _run([cli, *c], home=home, timeout=240)   # first call on a fresh home migrates config
        if rc != 0 and "already enabled" not in out:
            ok = False
            log.append(f"failed: hermes {' '.join(c)}: {out.strip()[-300:]}")
    if ok:
        log.append("settings written, plugin enabled")
    return ok, log


def restart_gateway(home: Path, cli: str | None = None) -> tuple[bool, str]:
    """Restart that profile's messaging gateway if it runs as a service (best effort)."""
    cli = cli or hermes_cli()
    if not cli:
        return False, "hermes command not found"
    rc, out = _run([cli, "gateway", "status"], home=Path(home), timeout=30)
    if rc != 0 or not re.search(r"\b(running|active)\b", out, re.I) or re.search(r"not running|inactive", out, re.I):
        return False, "gateway not running as a service (nothing to restart)"
    rc, out = _run([cli, "gateway", "restart"], home=Path(home), timeout=120)
    return rc == 0, out.strip()[-300:]


def temperament_for(profile: dict, name: str | None = None, description: str | None = None) -> dict:
    t = {"name": (name or profile.get("name") or profile["id"]).strip()[:64]}
    d = description if description is not None else profile.get("description", "")
    if d:
        t["description"] = d.strip()[:400]
    return t
