"""One-command supervisor for the complete Stack-chan laptop runtime."""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import urlopen

from .config import PROJECT_ROOT, Settings


@dataclass
class ManagedProcess:
    name: str
    process: subprocess.Popen[bytes]
    log_path: Path
    log_file: BinaryIO

    def stop(self) -> None:
        if self.process.poll() is None:
            try:
                os.killpg(self.process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                self.process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(self.process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                self.process.wait(timeout=3)
        self.log_file.close()


def _get_json(url: str) -> dict[str, object] | None:
    try:
        with urlopen(url, timeout=1.0) as response:  # noqa: S310 - fixed local endpoint
            raw = response.read()
    except HTTPError as error:
        raw = error.read()
    except (OSError, URLError):
        return None
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _eve_ready(base_url: str) -> bool:
    payload = _get_json(f"{base_url.rstrip('/')}/eve/v1/info")
    return bool(payload and isinstance(payload.get("agent"), dict))


def _server_runtime_ready(health_url: str) -> bool:
    payload = _get_json(health_url)
    if not payload:
        return False
    dependencies = payload.get("dependencies")
    if not isinstance(dependencies, dict):
        return False
    runtime_dependencies = {
        name: ready for name, ready in dependencies.items() if name != "device"
    }
    return bool(runtime_dependencies) and all(
        ready is True for ready in runtime_dependencies.values()
    )


def _device_connected(health_url: str) -> bool:
    payload = _get_json(health_url)
    dependencies = payload.get("dependencies") if payload else None
    return isinstance(dependencies, dict) and dependencies.get("device") is True


def _tail(path: Path, lines: int = 18) -> str:
    try:
        return "\n".join(path.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:])
    except OSError:
        return ""


def _start_process(name: str, command: list[str], cwd: Path, log_path: Path) -> ManagedProcess:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_file = log_path.open("ab", buffering=0)
    process = subprocess.Popen(  # noqa: S603 - fixed project-owned executables
        command,
        cwd=cwd,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    return ManagedProcess(name, process, log_path, log_file)


def _wait_ready(
    service: ManagedProcess,
    ready: Callable[[], bool],
    timeout_seconds: float,
) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if ready():
            return
        return_code = service.process.poll()
        if return_code is not None:
            detail = _tail(service.log_path)
            raise RuntimeError(
                f"{service.name} exited with {return_code}.\n{detail}".rstrip()
            )
        time.sleep(0.2)
    detail = _tail(service.log_path)
    raise RuntimeError(f"{service.name} did not become ready.\n{detail}".rstrip())


def _eve_command(eve_url: str) -> tuple[list[str], Path]:
    parsed = urlparse(eve_url)
    if parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise RuntimeError(
            f"Eve is configured at {eve_url}; start that remote service before Stack-chan."
        )
    port = parsed.port or 2000
    intelligence_dir = PROJECT_ROOT / "intelligence"
    executable = intelligence_dir / "node_modules/.bin/eve"
    if not executable.exists():
        npm = shutil.which("npm")
        if not npm:
            raise RuntimeError("npm is unavailable; run `pixi install` first.")
        print("Preparing intelligence for the first run...", flush=True)
        subprocess.run(  # noqa: S603 - resolved npm executable
            [npm, "ci", "--silent"],
            cwd=intelligence_dir,
            check=True,
        )
    return [str(executable), "dev", "--no-ui", "--port", str(port)], intelligence_dir


def _monitor(
    services: list[ManagedProcess],
    health_url: str,
    device_connected: bool,
) -> None:
    if not services:
        print("Stack-chan is already running.", flush=True)
        return
    next_device_probe = time.monotonic()
    while True:
        for service in services:
            return_code = service.process.poll()
            if return_code is not None:
                detail = _tail(service.log_path)
                raise RuntimeError(
                    f"{service.name} stopped with {return_code}.\n{detail}".rstrip()
                )
        now = time.monotonic()
        if now >= next_device_probe:
            current_device_connected = _device_connected(health_url)
            if current_device_connected != device_connected:
                if current_device_connected:
                    print("✓ Stack-chan connected", flush=True)
                else:
                    print("○ Stack-chan disconnected; waiting to reconnect", flush=True)
                device_connected = current_device_connected
            next_device_probe = now + 2
        time.sleep(0.5)


def run() -> None:
    settings = Settings()
    eve_url = settings.eve_url.rstrip("/")
    health_host = "127.0.0.1" if settings.host in {"0.0.0.0", "::"} else settings.host
    health_url = f"http://{health_host}:{settings.port}/health"
    log_dir = PROJECT_ROOT / "artifacts/logs"
    owned: list[ManagedProcess] = []

    print("Starting Stack-chan...", flush=True)
    try:
        if not _eve_ready(eve_url):
            eve_command, intelligence_dir = _eve_command(eve_url)
            eve = _start_process(
                "Intelligence",
                eve_command,
                intelligence_dir,
                log_dir / "eve.log",
            )
            owned.append(eve)
            _wait_ready(eve, lambda: _eve_ready(eve_url), 45)
        print("✓ Intelligence ready", flush=True)

        if not _server_runtime_ready(health_url):
            server_executable = shutil.which("stackchan-server")
            if not server_executable:
                raise RuntimeError("Stack-chan server is unavailable; run `pixi install` first.")
            server = _start_process(
                "Realtime server",
                [server_executable],
                PROJECT_ROOT / "server",
                log_dir / "server.log",
            )
            owned.append(server)
            _wait_ready(server, lambda: _server_runtime_ready(health_url), 90)
        print("✓ Local speech and memory ready", flush=True)

        connected = _device_connected(health_url)
        if connected:
            print("✓ Stack-chan connected", flush=True)
        else:
            print("○ Waiting for Stack-chan to connect", flush=True)
        print(f"Ready · logs: {log_dir}", flush=True)
        _monitor(owned, health_url, connected)
    finally:
        for service in reversed(owned):
            service.stop()


def main() -> None:
    try:
        run()
    except KeyboardInterrupt:
        print("\nStack-chan stopped.", flush=True)
    except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
        print(f"Could not start Stack-chan: {error}", file=sys.stderr)
        raise SystemExit(1) from error


if __name__ == "__main__":
    main()
