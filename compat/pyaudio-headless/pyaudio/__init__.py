"""Headless PyAudio compatibility surface.

MaAI imports :mod:`pyaudio` unconditionally even when callers provide their
own PCM source. Stack-chan does that over an isolated IPC bridge, so opening a
second laptop microphone would be both unnecessary and acoustically wrong.
"""


class PyAudio:
    def __init__(self, *args: object, **kwargs: object) -> None:
        raise RuntimeError(
            "Direct PyAudio capture is unavailable in the Stack-chan MaAI sidecar; "
            "use the server PCM bridge"
        )


paFloat32 = 1
