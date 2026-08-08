import asyncio
import math
import struct
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TurnContext:
    transcript: str
    language: str
    memories: Sequence[str]
    action_results: Sequence[str] = ()
    recent_turns: Sequence[tuple[str, str]] = ()


@dataclass(frozen=True, slots=True)
class PendingToolApproval:
    """Safe presentation metadata for one session-scoped tool approval."""

    request_id: str
    tool_name: str
    action_summary: str
    challenge: str
    seconds_remaining: float


class STTProvider(ABC):
    @abstractmethod
    async def transcribe(self, pcm16: bytes, sample_rate: int) -> tuple[str, str]: ...


class LLMProvider(ABC):
    @abstractmethod
    async def generate(self, context: TurnContext) -> AsyncIterator[str]:
        yield ""

    def cancel(self) -> None:
        """Request cancellation when the provider supports durable turns."""
        return None

    def pending_tool_approval(self) -> PendingToolApproval | None:
        """Return a pending approval without exposing its potentially sensitive input."""
        return None

    def blocks_normal_turn(self) -> bool:
        """Whether input must bypass normal memory and physical action routing."""
        return False

    async def aclose(self) -> None:
        """Release provider-owned sessions or network clients."""
        return None


class TTSProvider(ABC):
    sample_rate: int = 24_000

    @abstractmethod
    async def synthesize(self, text: str, language: str) -> AsyncIterator[bytes]:
        yield b""


class MockSTT(STTProvider):
    def __init__(self, transcript: str = "Hello Stack-chan") -> None:
        self.transcript = transcript

    async def transcribe(self, pcm16: bytes, sample_rate: int) -> tuple[str, str]:
        await asyncio.sleep(0)
        language = "ja" if any(ord(char) > 0x3000 for char in self.transcript) else "en"
        return self.transcript, language


class MockLLM(LLMProvider):
    async def generate(self, context: TurnContext) -> AsyncIterator[str]:
        response = (
            f"聞こえました。{context.transcript}"
            if context.language == "ja"
            else f"I heard you say: {context.transcript}"
        )
        for token in response.split(" "):
            await asyncio.sleep(0)
            yield token + " "


class MockTTS(TTSProvider):
    """Deterministic PCM generator for integration tests; not a production voice."""

    async def synthesize(self, text: str, language: str) -> AsyncIterator[bytes]:
        frame_samples = self.sample_rate // 50
        duration_frames = max(2, min(25, len(text) // 4))
        phase = 0
        for _ in range(duration_frames):
            samples = []
            for _ in range(frame_samples):
                value = int(6000 * math.sin(phase * 2 * math.pi * 440 / self.sample_rate))
                samples.append(value)
                phase += 1
            await asyncio.sleep(0)
            yield struct.pack(f"<{len(samples)}h", *samples)
