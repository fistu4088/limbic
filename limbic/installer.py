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


def run_wizard(source: Path, input_fn=input, secret_fn=getpass.getpass,
               platform: str | None = None) -> int:
    system = platform or os.name
    print("\nlimbic setup — interactive installer")
    print("This installs a per-user copy. It does not need administrator access.\n")
    default_dir = str(default_install_dir(system))
    target = Path(_ask("Install directory", default_dir, input_fn)).expanduser()
    host = _ask("Bind address (127.0.0.1 = this computer only)", "127.0.0.1", input_fn)
    try:
        socket.getaddrinfo(host, None, socket.AF_INET, socket.SOCK_STREAM)
    except OSError:
        print(f"Could not resolve bind address {host!r}; using 127.0.0.1.")
        host = "127.0.0.1"
    if host not in ("127.0.0.1", "localhost"):
        print("Warning: dashboard and read-only API endpoints are not authenticated. Do not expose this server to an untrusted network.")
        if _choose("Continue with this bind address?", {"y": "Yes, I have a trusted network and firewall", "n": "Use localhost only"}, "n", input_fn) == "n":
            host = "127.0.0.1"
    while True:
        try:
            port = int(_ask("Port", "8770", input_fn))
            if not 1 <= port <= 65535:
                raise ValueError
        except ValueError:
            print("Enter a port number from 1 to 65535.")
            continue
        if _port_available(host, port):
            break
        print(f"Port {port} is already in use on {host}; choose another.")
    agent_id = _ask("Agent ID", "default", input_fn)
    agent_name = _ask("Agent display name", agent_id.title() or "Agent", input_fn)
    llm_url, model, api_key = _appraiser(input_fn, secret_fn)
    config = build_config(host, port, agent_id, llm_url, model, api_key, agent_name=agent_name)
    config_path = write_installation(source, target, config)
    print(f"\nInstalled to: {target}")
    print(f"Private settings and write token: {config_path}")
    print(f"Database: {target / 'data' / 'limbic.db'}")
    print(f"Log: {target / 'data' / 'limbic.log'}")
    start_now = _choose("Start the server now?", {"y": "Yes", "n": "No"}, "y", input_fn)
    if start_now == "y":
        process, url = _start_server(target, config)
        if process is None:
            print("Server did not become ready. Check the log file above.")
            return 1
        try:
            create_agent(url, config)
        except (OSError, ValueError, urllib.error.URLError) as exc:
            process.terminate()
            print(f"Server started, but agent setup failed: {exc}", file=sys.stderr)
            print(f"Inspect the log: {target / 'data' / 'limbic.log'}", file=sys.stderr)
            return 1
        (target / "data" / "limbic.pid").write_text(str(process.pid) + "\n", encoding="ascii")
        print(f"Server is running: {url} (PID {process.pid})")
        print(f"Agent created: {config['agent_id']} ({config['agent_name']})")
        print(f"Dashboard: {url}/")
        if os.name == "nt":
            print(f"Stop it with: Stop-Process -Id {process.pid}")
        else:
            print(f"Stop it with: kill {process.pid}")
        try:
            webbrowser.open(url)
        except Exception:
            pass
    else:
        print(f"Start it later with: {sys.executable} {target / 'start-limbic.py'}")
    print("Setup complete.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Interactive limbic installer")
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parent.parent,
                        help="limbic source directory (defaults to this checkout)")
    args = parser.parse_args(argv)
    try:
        return run_wizard(args.source)
    except (OSError, ValueError) as exc:
        print(f"Installation stopped: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
