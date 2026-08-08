import asyncio
from pathlib import Path

import httpx


class WhisperServerProcess:
    """Own a whisper-server process unless a compatible local service already exists."""

    def __init__(
        self,
        executable: Path,
        model: Path,
        host: str,
        port: int,
        log_path: Path,
        threads: int = 8,
    ) -> None:
        self.executable = executable
        self.model = model
        self.host = host
        self.port = port
        self.log_path = log_path
        self.threads = threads
        self.process: asyncio.subprocess.Process | None = None
        self._log = None

    async def _ready(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=0.5) as client:
                response = await client.get(f"http://{self.host}:{self.port}/health")
            return response.status_code == 200 and response.json().get("status") == "ok"
        except (httpx.HTTPError, ValueError):
            return False

    async def start(self) -> None:
        if await self._ready():
            return
        if not self.executable.exists():
            raise RuntimeError(f"whisper-server not found: {self.executable}")
        if not self.model.exists():
            raise RuntimeError(f"whisper model not found: {self.model}")
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._log = self.log_path.open("ab", buffering=0)
        self.process = await asyncio.create_subprocess_exec(
            str(self.executable),
            "-m",
            str(self.model),
            "--host",
            self.host,
            "--port",
            str(self.port),
            "-l",
            "auto",
            "-nt",
            "-t",
            str(self.threads),
            "-bo",
            "1",
            "-bs",
            "1",
            "-nf",
            "-nlp",
            stdout=self._log,
            stderr=self._log,
        )
        for _ in range(120):
            if await self._ready():
                return
            if self.process.returncode is not None:
                raise RuntimeError(
                    f"whisper-server exited with {self.process.returncode}; see {self.log_path}"
                )
            await asyncio.sleep(0.1)
        await self.stop()
        raise RuntimeError(f"whisper-server did not become ready; see {self.log_path}")

    async def stop(self) -> None:
        if self.process and self.process.returncode is None:
            self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), timeout=5)
            except TimeoutError:
                self.process.kill()
                await self.process.wait()
        self.process = None
        if self._log:
            self._log.close()
            self._log = None


class SupertonicServerProcess:
    def __init__(self, executable: Path, host: str, port: int, log_path: Path) -> None:
        self.executable = executable
        self.host = host
        self.port = port
        self.log_path = log_path
        self.process: asyncio.subprocess.Process | None = None
        self._log = None

    async def _ready(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=0.5) as client:
                response = await client.get(f"http://{self.host}:{self.port}/v1/health")
            return response.status_code == 200
        except httpx.HTTPError:
            return False

    async def start(self) -> None:
        if await self._ready():
            return
        if not self.executable.exists():
            raise RuntimeError(f"Supertonic CLI not found: {self.executable}")
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._log = self.log_path.open("ab", buffering=0)
        self.process = await asyncio.create_subprocess_exec(
            str(self.executable),
            "serve",
            "--host",
            self.host,
            "--port",
            str(self.port),
            stdout=self._log,
            stderr=self._log,
        )
        for _ in range(600):
            if await self._ready():
                return
            if self.process.returncode is not None:
                raise RuntimeError(
                    f"Supertonic exited with {self.process.returncode}; see {self.log_path}"
                )
            await asyncio.sleep(0.1)
        await self.stop()
        raise RuntimeError(f"Supertonic did not become ready; see {self.log_path}")

    async def stop(self) -> None:
        if self.process and self.process.returncode is None:
            self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), timeout=5)
            except TimeoutError:
                self.process.kill()
                await self.process.wait()
        self.process = None
        if self._log:
            self._log.close()
            self._log = None
