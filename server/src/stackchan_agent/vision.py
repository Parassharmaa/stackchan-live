"""Optional local macOS Vision analysis for explicit camera captures."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from PIL import Image, ImageStat


def summarize_vision(payload: dict) -> str:
    face_count = max(0, int(payload.get("faceCount", 0)))
    labels = [
        str(item.get("name", "")).replace("_", " ")
        for item in payload.get("labels", [])
        if isinstance(item, dict)
        and float(item.get("confidence", 0.0)) >= 0.35
        and item.get("name")
    ][:3]
    text = [str(item).strip() for item in payload.get("text", []) if str(item).strip()][:3]
    observations: list[str] = []
    if face_count:
        observations.append(f"detected {face_count} {'face' if face_count == 1 else 'faces'}")
    if labels:
        observations.append("likely scene labels: " + ", ".join(labels))
    if text:
        observations.append("readable text: " + " | ".join(text))
    if not observations:
        return "local vision could not identify the scene confidently"
    return "; ".join(observations)


class AppleVisionAnalyzer:
    def __init__(self, source: Path, binary: Path) -> None:
        self.source = source
        self.binary = binary
        self._build_lock = asyncio.Lock()

    async def _ensure_binary(self) -> bool:
        if sys.platform != "darwin" or not self.source.exists():
            return False
        if self.binary.exists() and self.binary.stat().st_mtime >= self.source.stat().st_mtime:
            return True
        async with self._build_lock:
            if self.binary.exists() and self.binary.stat().st_mtime >= self.source.stat().st_mtime:
                return True
            self.binary.parent.mkdir(parents=True, exist_ok=True)
            process = await asyncio.create_subprocess_exec(
                "xcrun",
                "swiftc",
                str(self.source),
                "-O",
                "-o",
                str(self.binary),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                await asyncio.wait_for(process.communicate(), timeout=20.0)
            except TimeoutError:
                process.kill()
                await process.communicate()
                return False
            return process.returncode == 0 and self.binary.exists()

    async def analyze(self, image_path: Path) -> dict:
        if not await self._ensure_binary():
            return {}
        process = await asyncio.create_subprocess_exec(
            str(self.binary),
            str(image_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, _ = await asyncio.wait_for(process.communicate(), timeout=8.0)
        except TimeoutError:
            process.kill()
            await process.communicate()
            return {}
        if process.returncode != 0:
            return {}
        try:
            payload = json.loads(stdout)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {}
        if not isinstance(payload, dict):
            return {}
        mean_luma, luma_stddev = await asyncio.to_thread(_image_luma, image_path)
        payload["meanLuma"] = round(mean_luma, 2)
        payload["lumaStddev"] = round(luma_stddev, 2)
        if mean_luma < 25 or luma_stddev < 2:
            return {
                **payload,
                "summary": "the camera frame was too dark or flat to analyze reliably",
            }
        return {**payload, "summary": summarize_vision(payload)}


def _image_luma(image_path: Path) -> tuple[float, float]:
    with Image.open(image_path) as image:
        statistics = ImageStat.Stat(image.convert("L"))
    return float(statistics.mean[0]), float(statistics.stddev[0])
