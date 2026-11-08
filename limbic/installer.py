"""Interactive, cross-platform limbic installer. Run with ``python -m limbic.installer``."""
from __future__ import annotations

import argparse
import getpass
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path


PROVIDERS = {
    "1": ("OpenAI", "https://api.openai.com/v1", "gpt-4o-mini"),
    "2": ("OpenRouter", "https://openrouter.ai/api/v1", "google/gemini-2.5-flash"),
    "3": ("Google Gemini", "https://generativelanguage.googleapis.com/v1beta/openai", "gemini-flash-lite-latest"),
    "4": ("DeepSeek", "https://api.deepseek.com/v1", "deepseek-chat"),
    "5": ("Custom OpenAI-compatible endpoint", "", ""),
}


LAUNCHER = '''"""Start the locally installed limbic server in this tracked process."""
import json
import os
import sys
from pathlib import Path

home = Path(__file__).resolve().parent
config = json.loads((home / "data" / "limbic-install.json").read_text(encoding="utf-8"))
os.environ.update(config["env"])
sys.argv = ["limbic", "serve", "--db", config["db"],
            "--host", config["host"], "--port", str(config["port"])]
from limbic.cli import main
raise SystemExit(main())
'''


def normalize_appraiser_url(value: str) -> str:
    """Accept an OpenAI-compatible base URL or its full chat-completions URL."""
    value = value.strip().rstrip("/")
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("URL must start with http:// or https:// and include a host")
    path = parsed.path.rstrip("/")
    suffix = "/chat/completions"
    if path.endswith(suffix):
        path = path[:-len(suffix)]
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, path, "", "")).rstrip("/")


def default_install_dir(platform: str | None = None, environ: dict[str, str] | None = None) -> Path:
    """Return a per-user application directory without requiring administrator access."""
    system = platform or os.name
    env = environ if environ is not None else os.environ
    if system in ("nt", "win32", "Windows"):
        base = Path(env.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
    else:
        base = Path(env.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share"))
    return base / "limbic"


def build_config(host: str, port: int, agent_id: str, llm_url: str,
                 model: str = "", api_key: str = "", token: str | None = None,
                 agent_name: str | None = None) -> dict:
    if not 1 <= int(port) <= 65535:
        raise ValueError("port must be between 1 and 65535")
    agent_id = agent_id.strip()
    if not agent_id or len(agent_id) > 64 or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-" for c in agent_id):
        raise ValueError("agent ID must contain 1–64 letters, numbers, dots, underscores or hyphens")
    env = {"LIMBIC_TOKEN": token or secrets.token_hex(32)}
    if llm_url:
        env["LIMBIC_LLM_URL"] = normalize_appraiser_url(llm_url)
        env["LIMBIC_LLM_MODEL"] = model.strip()
        if api_key:
            env["LIMBIC_LLM_KEY"] = api_key
    return {"format": 1, "host": host.strip() or "127.0.0.1", "port": int(port),
            "agent_id": agent_id, "agent_name": (agent_name or agent_id.title()).strip(), "env": env}


def write_installation(source: str | Path, target: str | Path, config: dict) -> Path:
    """Copy the project and write private config; refuse to overwrite existing files."""
    source, target = Path(source).resolve(), Path(target).expanduser().resolve()
    if not (source / "limbic" / "__init__.py").is_file():
        raise FileNotFoundError(f"Not a limbic source directory: {source}")
    if target.exists() and any(target.iterdir()):
        raise FileExistsError(f"Install directory is not empty: {target}")
    target.mkdir(parents=True, exist_ok=True)
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc", ".venv", ".pytest_cache", "data", "build", "dist")
    shutil.copytree(source, target, dirs_exist_ok=True, ignore=ignore)
    data_dir = target / "data"
    data_dir.mkdir(exist_ok=True)
    installed = dict(config)
    installed["db"] = str(data_dir / "limbic.db")
    config_path = data_dir / "limbic-install.json"
    config_path.write_text(json.dumps(installed, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (target / "start-limbic.py").write_text(LAUNCHER, encoding="utf-8")
    if os.name != "nt":
        try:
            data_dir.chmod(0o700)
            config_path.chmod(0o600)
        except OSError:
            pass
    return config_path


def _ask(label: str, default: str, input_fn=input) -> str:
    value = input_fn(f"{label} [{default}]: ").strip()
    return value or default


def _choose(label: str, options: dict[str, str], default: str, input_fn=input) -> str:
    for key, text in options.items():
        print(f"  {key}) {text}")
    while True:
        value = input_fn(f"{label} [{default}]: ").strip() or default
        if value in options:
            return value
        print("Please choose one of the listed numbers.")


def _appraiser(input_fn=input, secret_fn=getpass.getpass) -> tuple[str, str, str]:
    print("\nAppraiser model (optional; without one, limbic uses its built-in basic evaluator):")
    mode = _choose("Choose", {"0": "Skip for now", "1": "Hosted API", "2": "Local OpenAI-compatible server"}, "0", input_fn)
    if mode == "0":
        return "", "", ""
    if mode == "2":
        url = normalize_appraiser_url(_ask("Local endpoint URL", "http://127.0.0.1:11434/v1", input_fn))
        model = _ask("Model name", "qwen2.5:7b-instruct", input_fn)
        key = secret_fn("API key (leave blank if not required): ")
        return url, model, key
    print("\nHosted provider presets:")
    choice = _choose("Provider", {k: v[0] for k, v in PROVIDERS.items()}, "1", input_fn)
    name, preset_url, preset_model = PROVIDERS[choice]
    url = normalize_appraiser_url(_ask("API base URL", preset_url or "https://api.example.com/v1", input_fn))
    model = _ask("Model name", preset_model or "your-model-name", input_fn)
    key = secret_fn(f"{name} API key (input is hidden): ")
    if not key:
        print("No API key entered. Try another provider or choose local/skip.")
        return _appraiser(input_fn, secret_fn)
    return url, model, key


def _open_local(url_or_request, timeout: float):
    """Call the local server directly; system HTTP proxies must not intercept loopback."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    return opener.open(url_or_request, timeout=timeout)


def create_agent(base_url: str, config: dict) -> dict:
    """Create the configured agent through the authenticated local API."""
    payload = {"id": config["agent_id"],
               "temperament": {"name": config.get("agent_name") or config["agent_id"].title()}}
    req = urllib.request.Request(
        base_url.rstrip("/") + "/api/agents", data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {config['env']['LIMBIC_TOKEN']}",
                 "Content-Type": "application/json"}, method="POST")
    with _open_local(req, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def _port_available(host: str, port: int) -> bool:
    try:
        with socket.socket() as sock:
            sock.bind((host, port))
        return True
    except OSError:
        return False


def _start_server(target: Path, config: dict) -> tuple[subprocess.Popen | None, str]:
    log_path = target / "data" / "limbic.log"
    log = log_path.open("ab")
    cmd = [sys.executable, str(target / "start-limbic.py")]
    kwargs = {"cwd": str(target), "stdout": log, "stderr": subprocess.STDOUT,
              "stdin": subprocess.DEVNULL, "close_fds": True}
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200) | getattr(subprocess, "DETACHED_PROCESS", 0x8)
    else:
        kwargs["start_new_session"] = True
    try:
        process = subprocess.Popen(cmd, **kwargs)
    finally:
        log.close()
    url = f"http://{config['host']}:{config['port']}"
    for _ in range(60):
        if process.poll() is not None:
            return None, url
        try:
            with _open_local(url + "/api/health", timeout=0.5) as response:
                if response.status == 200:
                    return process, url
        except (OSError, urllib.error.URLError):
            time.sleep(0.25)
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)
    return None, url


# ------------------------------------------------------------------ upgrade / autostart
def existing_config(target: Path) -> dict | None:
    path = Path(target) / "data" / "limbic-install.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def upgrade_installation(source: str | Path, target: str | Path) -> Path:
    """Refresh the program files of an existing install; data/ (db, token, settings) stays."""
    source, target = Path(source).resolve(), Path(target).expanduser().resolve()
    if not (source / "limbic" / "__init__.py").is_file():
        raise FileNotFoundError(f"Not a limbic source directory: {source}")
    if existing_config(target) is None:
        raise FileNotFoundError(f"No limbic installation in {target}")
    if source != target:
        ignore = shutil.ignore_patterns("__pycache__", "*.pyc", ".venv", ".pytest_cache", "data", "build", "dist", ".git")
        shutil.copytree(source, target, dirs_exist_ok=True, ignore=ignore)
    (target / "start-limbic.py").write_text(LAUNCHER, encoding="utf-8")
    return target / "data" / "limbic-install.json"


def _pythonw() -> str:
    exe = Path(sys.executable)
    w = exe.with_name("pythonw.exe")
    return str(w if w.is_file() else exe)


def _systemd_user_ok() -> bool:
    if os.name == "nt" or sys.platform == "darwin" or not shutil.which("systemctl"):
        return False
    try:
        return subprocess.run(["systemctl", "--user", "show-environment"], capture_output=True,
                              timeout=10).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def install_autostart(target: Path) -> tuple[str, Path | None]:
    """Start limbic at login for this user. Returns (kind, file) — kind '' if unsupported."""
    target = Path(target)
    launcher = target / "start-limbic.py"
    if os.name == "nt":
        appdata = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
        startup = appdata / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
        startup.mkdir(parents=True, exist_ok=True)
        f = startup / "limbic.vbs"
        f.write_text('CreateObject("WScript.Shell").Run """' + _pythonw() + '"" ""' + str(launcher)
                     + '""", 0, False\r\n', encoding="utf-8")
        return "windows-startup", f
    if sys.platform == "darwin":
        f = Path.home() / "Library" / "LaunchAgents" / "io.limbic.server.plist"
        f.parent.mkdir(parents=True, exist_ok=True)
        log = target / "data" / "limbic.log"
        f.write_text(f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>Label</key><string>io.limbic.server</string>
<key>ProgramArguments</key><array><string>{sys.executable}</string><string>{launcher}</string></array>
<key>WorkingDirectory</key><string>{target}</string>
<key>RunAtLoad</key><true/><key>KeepAlive</key><true/>
<key>StandardOutPath</key><string>{log}</string><key>StandardErrorPath</key><string>{log}</string>
</dict></plist>
""", encoding="utf-8")
        subprocess.run(["launchctl", "unload", str(f)], capture_output=True)
        subprocess.run(["launchctl", "load", "-w", str(f)], capture_output=True)
        return "launchd", f
    if _systemd_user_ok():
        f = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "systemd" / "user" / "limbic.service"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(f"""[Unit]
Description=limbic emotion engine
After=network.target

[Service]
WorkingDirectory={target}
ExecStart={sys.executable} {launcher}
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
""", encoding="utf-8")
        subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True)
        subprocess.run(["systemctl", "--user", "enable", "limbic.service"], capture_output=True)
        return "systemd-user", f
    return "", None


def _health(url: str) -> bool:
    try:
        with _open_local(url + "/api/health", timeout=0.7) as response:
            return response.status == 200
    except (OSError, urllib.error.URLError):
        return False


def _wait_health(url: str, seconds: float = 15) -> bool:
    end = time.time() + seconds
    while time.time() < end:
        if _health(url):
            return True
        time.sleep(0.25)
    return False


def _stop_running(target: Path, url: str, autostart: str) -> None:
    """Stop an already running copy so the upgraded code is what runs."""
    if autostart == "systemd-user":
        subprocess.run(["systemctl", "--user", "stop", "limbic.service"], capture_output=True)
    pid_file = Path(target) / "data" / "limbic.pid"
    try:
        pid = int(pid_file.read_text().strip())
    except (OSError, ValueError):
        pid = 0
    if pid and _health(url):
        try:
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True)
            else:
                os.kill(pid, 15)
        except OSError:
            pass
    end = time.time() + 8
    while time.time() < end and _health(url):
        time.sleep(0.25)


def _launch(target: Path, config: dict, autostart: str) -> tuple[bool, str]:
    url = f"http://{'127.0.0.1' if config['host'] in ('0.0.0.0', '::') else config['host']}:{config['port']}"
    if autostart == "systemd-user":
        subprocess.run(["systemctl", "--user", "restart", "limbic.service"], capture_output=True)
        return _wait_health(url), url
    if autostart == "launchd":
        return _wait_health(url), url
    process, _ = _start_server(target, config)
    if process is None:
        return False, url
    (Path(target) / "data" / "limbic.pid").write_text(str(process.pid) + "\n", encoding="ascii")
    return True, url


def _api_post(url: str, path: str, token: str, body: dict, timeout: float = 900) -> dict:
    req = urllib.request.Request(url.rstrip("/") + path, data=json.dumps(body).encode("utf-8"), method="POST",
                                 headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    try:
        with _open_local(req, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            return json.loads(exc.read().decode("utf-8"))
        except ValueError:
            return {"error": str(exc)}


def _free_port(host: str, start: int = 8770) -> int:
    for port in range(start, start + 30):
        if _port_available(host, port):
            return port
    return start


def _yes(label: str, default: bool, input_fn=input, auto: bool = False) -> bool:
    if auto:
        return default
    hint = "Y/n" if default else "y/N"
    while True:
        v = input_fn(f"{label} [{hint}]: ").strip().lower()
        if not v:
            return default
        if v in ("y", "yes", "i", "igen"):
            return True
        if v in ("n", "no", "nem"):
            return False


def _pick_profiles(profiles: list[dict], input_fn=input, auto: bool = False, preset: str | None = None) -> list[dict]:
    if not profiles:
        return []
    print("\nHermes Agent found. Profiles on this computer:")
    for n, p in enumerate(profiles, 1):
        state = (f"already connected to {p['url'] or 'limbic'} - skipped unless picked by number"
                 if p["plugin_enabled"] else ("plugin present" if p["plugin_installed"] else ""))
        print(f"  {n}) {p['id']:<20} {p['name']}" + (f"  ({state})" if state else ""))
    while True:
        v = (preset if preset is not None else ("all" if auto else
             input_fn("Give feelings to which profiles? Enter = all, e.g. 1,3 = some, n = none: "))).strip().lower()
        if v in ("", "all", "a", "mind"):
            return [p for p in profiles if not p["plugin_enabled"]]   # never re-point a working setup by default
        if v in ("n", "no", "none", "nem", "0"):
            return []
        try:
            picked = []
            for part in v.replace(" ", "").split(","):
                if part.isdigit():
                    picked.append(profiles[int(part) - 1])
                else:
                    picked.append(next(p for p in profiles if p["id"] == part))
            return picked
        except (IndexError, StopIteration):
            print("Unknown choice.")
            if preset is not None or auto:
                return []


def run_wizard(source: Path, input_fn=input, secret_fn=getpass.getpass,
               platform: str | None = None, auto: bool = False, target_dir: str | None = None,
               profiles_arg: str | None = None, autostart: bool | None = None,
               restart_hermes: bool | None = None, open_browser: bool = True) -> int:
    from . import hermes
    system = platform or os.name
    print("\nlimbic setup")
    print("Installs for this user only; no administrator rights needed.\n")

    target = Path(target_dir or default_install_dir(system)).expanduser()
    old = existing_config(target)
    root = hermes.find_hermes_root()
    profiles = hermes.list_profiles(root)

    custom = False
    if not auto:
        if old:
            print(f"An existing installation was found in {target}; it will be updated, your data stays.")
        v = input_fn("Press Enter for the recommended setup, or type c to customise: ").strip().lower()
        custom = v in ("c", "customise", "customize", "egyéni")

    if old:
        config = old
    else:
        host, port = "127.0.0.1", _free_port("127.0.0.1")
        if custom:
            target = Path(_ask("Install directory", str(target), input_fn)).expanduser()
            host = _ask("Bind address (127.0.0.1 = this computer only)", host, input_fn)
            if host not in ("127.0.0.1", "localhost"):
                print("Warning: the dashboard and read-only API are not authenticated. Only use a trusted network.")
                if not _yes("Keep this bind address?", False, input_fn):
                    host = "127.0.0.1"
            while True:
                try:
                    port = int(_ask("Port", str(port), input_fn))
                    if 1 <= port <= 65535 and _port_available(host, port):
                        break
                except ValueError:
                    pass
                print("Enter a free port number from 1 to 65535.")
        config = build_config(host, port, "default", "", agent_name="Agent")

    chosen = _pick_profiles(profiles, input_fn, auto, profiles_arg)
    if not profiles:
        print("Hermes Agent was not found on this computer; limbic runs on its own"
              " (connect it later from the dashboard's Hermes button or docs/HERMES.md).")

    # appraiser: keep an existing one; else offer the Hermes profile's own endpoint; else menu/skip
    if not old or "LIMBIC_LLM_URL" not in config.get("env", {}):
        url = model = key = ""
        offer = next((hermes.appraiser_from_profile(Path(p["home"])) for p in chosen
                      if hermes.appraiser_from_profile(Path(p["home"]))), {})
        if offer and _yes(f"Use the model Hermes already uses ({offer['model']} at {offer['url']}) to appraise feelings?",
                          True, input_fn, auto):
            url, model, key = offer["url"], offer["model"], offer["key"]
        elif custom:
            url, model, key = _appraiser(input_fn, secret_fn)
        else:
            print("No appraiser model set: limbic starts with its basic built-in evaluator."
                  " You can add a model later (docs/INSTALLATION.md, step 3).")
        if url:
            config["env"].update({"LIMBIC_LLM_URL": normalize_appraiser_url(url), "LIMBIC_LLM_MODEL": model})
            if key:
                config["env"]["LIMBIC_LLM_KEY"] = key

    want_autostart = autostart if autostart is not None else _yes("Start limbic automatically when you log in?", True, input_fn, auto)

    # ---- do it
    if old:
        config_path = upgrade_installation(source, target)
        config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    else:
        config_path = write_installation(source, target, config)
        config = json.loads(config_path.read_text(encoding="utf-8"))
    print(f"\nInstalled to: {target}")
    kind = ""
    if want_autostart:
        kind, f = install_autostart(target)
        print(f"Autostart: {f}" if kind else "Autostart is not supported here; start it with start-limbic.py.")
    url = f"http://127.0.0.1:{config['port']}" if config["host"] in ("0.0.0.0", "::") else f"http://{config['host']}:{config['port']}"
    if old:
        _stop_running(target, url, kind)
    ok, url = _launch(target, config, kind)
    if not ok:
        print(f"The server did not start. Log: {target / 'data' / 'limbic.log'}", file=sys.stderr)
        return 1
    print(f"limbic is running: {url}")
    token = config["env"]["LIMBIC_TOKEN"]

    connected = []
    for p in chosen:
        res = _api_post(url, f"/api/hermes/profiles/{urllib.parse.quote(p['id'])}/connect", token,
                        {"lang": p["lang"], "force": bool(p["plugin_enabled"])})
        if res.get("plugin"):
            connected.append(p)
            print(f"  ✓ {p['id']}: agent '{res['agent']}' {'created' if res.get('created') else 'kept'}, plugin enabled")
        else:
            print(f"  ✗ {p['id']}: {res.get('error') or 'plugin setup failed'}")
            for line in res.get("log", [])[-4:]:
                print("     " + line)
    if not chosen and not old:
        try:
            create_agent(url, config)
            print(f"Agent created: {config['agent_id']}")
        except (OSError, ValueError, urllib.error.URLError) as exc:
            print(f"Agent setup failed: {exc}", file=sys.stderr)

    if connected:
        if restart_hermes if restart_hermes is not None else _yes(
                "Restart the Hermes messaging gateway now so it loads the plugin?", True, input_fn, auto):
            for p in connected:
                done, msg = hermes.restart_gateway(Path(p["home"]))
                print(f"  {p['id']}: {'gateway restarted' if done else msg}")
        print("Hermes Desktop / chat windows that are already open: close and reopen them once.")

    print(f"\nDashboard: {url}/   (write token: {config_path})")
    if open_browser and not auto:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    print("Setup complete.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="limbic installer (next, next, finish)")
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parent.parent,
                        help="limbic source directory (defaults to this checkout)")
    parser.add_argument("--yes", "-y", action="store_true", help="no questions: recommended settings everywhere")
    parser.add_argument("--dir", default=None, help="install directory")
    parser.add_argument("--profiles", default=None, help="Hermes profiles to connect: all, none, or e.g. default,aria")
    parser.add_argument("--no-autostart", action="store_true", help="do not start limbic at login")
    parser.add_argument("--no-restart", action="store_true", help="do not restart the Hermes gateway")
    args = parser.parse_args(argv)
    try:
        return run_wizard(args.source, auto=args.yes, target_dir=args.dir, profiles_arg=args.profiles,
                          autostart=False if args.no_autostart else None,
                          restart_hermes=False if args.no_restart else None)
    except (OSError, ValueError) as exc:
        print(f"Installation stopped: {exc}", file=sys.stderr)
        return 1
    except (KeyboardInterrupt, EOFError):
        print("\nCancelled.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
