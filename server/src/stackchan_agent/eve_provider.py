import asyncio
import json
import re
import secrets
import time
import unicodedata
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from typing import Any

import httpx

from .local_providers import response_sentence_budget, visual_only_glyph
from .providers import LLMProvider, PendingToolApproval, TurnContext


@dataclass(frozen=True, slots=True)
class _EveToolApproval:
    request_id: str
    session_id: str
    tool_name: str
    action_summary: str
    challenge: str
    expires_at: float


_DENY_PHRASES = {
    "deny",
    "denied",
    "no",
    "no deny",
    "reject",
    "cancel",
    "拒否",
    "拒否します",
    "いいえ",
    "だめ",
    "ダメ",
    "キャンセル",
}

def classify_voice_approval(text: str, *, challenge: str | None = None) -> bool | None:
    """Classify only short, explicit approval phrases; unrelated speech is inert."""
    normalized = unicodedata.normalize("NFKC", text).casefold().strip()
    normalized = re.sub(r"[\s\u3000]+", " ", normalized)
    normalized = re.sub(r"[.,!?。！？、]+$", "", normalized).strip()
    if normalized in _DENY_PHRASES:
        return False
    if challenge is None or not re.fullmatch(r"\d{2}", challenge):
        return None
    escaped = re.escape(challenge)
    if re.fullmatch(
        rf"(?:i )?approve(?: that| the)? action(?: code)? {escaped}", normalized
    ) or re.fullmatch(rf"approve(?: code)? {escaped}", normalized):
        return True
    compact = normalized.replace(" ", "")
    if re.fullmatch(
        rf"(?:承認(?:コード)?{escaped}|(?:コード)?{escaped}を?(?:承認|許可)(?:します)?)",
        compact,
    ):
        return True
    return None


def _safe_tool_label(tool_name: str) -> str:
    label = re.sub(r"[^\w:.-]+", " ", tool_name, flags=re.UNICODE)
    return re.sub(r"[_:.-]+", " ", label).strip()[:48] or "requested tool"


def _voice_safe_action_summary(
    value: object, required_fields: tuple[str, ...] | None
) -> str | None:
    """Speak every allowlisted material field; unknown or incomplete shapes fail closed."""
    if not isinstance(value, dict) or not required_fields:
        return None
    if set(value) != set(required_fields):
        return None
    details: list[str] = []
    for key in required_fields:
        field = value[key]
        if not isinstance(field, (str, int, float, bool)):
            return None
        rendered = re.sub(r"[\x00-\x1f\x7f]+", " ", str(field)).strip()
        if not rendered:
            return None
        safe_key = key.replace("_", " ")
        details.append(f"{safe_key} {rendered[:64]}")
    return ", ".join(details) if details else None


def _display_only_challenge(*spoken_fragments: str) -> str:
    """Choose a short code that cannot occur anywhere in Stack-chan's prompt."""
    spoken = " ".join(spoken_fragments)
    candidates = [str(value) for value in range(10, 100) if str(value) not in spoken]
    if not candidates:
        raise RuntimeError("voice approval has no display-only challenge available")
    return secrets.choice(candidates)


def clean_spoken_delta(text: str) -> str:
    """Remove visual-only glyphs that should never reach streamed TTS."""
    cleaned = "".join(
        character
        for character in text
        if unicodedata.category(character)[0] != "C"
        and not visual_only_glyph(character)
        and character not in "#*`[]{}"
    )
    return re.sub(r" {2,}", " ", cleaned)


def bound_spoken_delta(
    emitted: str, delta: str, language: str, *, max_sentences: int = 3
) -> tuple[str, bool]:
    """Keep Eve expressive but bounded for an interruptible physical companion."""
    limit = 240 if language == "ja" else 480
    candidate = delta[: max(0, limit - len(emitted))]
    combined = emitted + candidate
    terminal_characters = "。！？" if language == "ja" else ".!?"
    terminal_indexes: list[int] = []
    for index, character in enumerate(combined):
        if character not in terminal_characters:
            continue
        if index > 0 and combined[index - 1] in terminal_characters:
            continue
        if index + 1 < len(combined) and combined[index + 1] in terminal_characters:
            continue
        terminal_indexes.append(index)
    if len(terminal_indexes) >= max_sentences:
        boundary = terminal_indexes[max_sentences - 1]
        return candidate[: boundary - len(emitted) + 1], True
    return candidate, len(combined) >= limit


class EveLLM(LLMProvider):
    """Durable Eve session adapter for the latency-sensitive Python pipeline."""

    def __init__(
        self,
        base_url: str,
        *,
        core_url: str = "http://127.0.0.1:8765",
        device_id: str | None = None,
        timeout_seconds: float = 90.0,
        approval_timeout_seconds: float = 30.0,
        approval_summary_fields: Mapping[str, tuple[str, ...]] | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        core_transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.core_url = core_url.rstrip("/")
        self.device_id = device_id
        self.timeout_seconds = timeout_seconds
        self.approval_timeout_seconds = approval_timeout_seconds
        self.approval_summary_fields = dict(approval_summary_fields or {})
        self.transport = transport
        self.core_transport = core_transport
        self.session_id: str | None = None
        self._registered_session_id: str | None = None
        self._cursor = 0
        self._waiting = True
        self._submitting = False
        self._active_turn_id: str | None = None
        self._cancel_pending = False
        self._cancel_tasks: set[asyncio.Task[None]] = set()
        self._pending_approval: _EveToolApproval | None = None
        self._pending_denial: _EveToolApproval | None = None
        self._approval_timeout_task: asyncio.Task[None] | None = None
        self._approval_response_lock = asyncio.Lock()

    async def warmup(self) -> None:
        """Pay Eve workflow/model cold-start cost before live device speech."""
        try:
            async for _ in self.generate(
                TurnContext(
                    "Reply with the single word ready.",
                    "en",
                    (),
                )
            ):
                pass
        finally:
            await self.aclose()

    async def bind_device(self, device_id: str) -> None:
        """Bind this durable Eve session to exactly one authenticated device."""
        self.device_id = device_id
        if self.session_id is not None:
            await self._register_device_binding(self.session_id)

    async def _register_device_binding(self, session_id: str) -> None:
        if self.device_id is None or self._registered_session_id == session_id:
            return
        async with httpx.AsyncClient(
            base_url=self.core_url,
            timeout=5.0,
            transport=self.core_transport,
        ) as client:
            response = await client.post(
                f"/v1/eve-sessions/{session_id}",
                json={"device_id": self.device_id},
            )
            response.raise_for_status()
        self._registered_session_id = session_id

    async def _unregister_device_binding(self, session_id: str) -> None:
        if self._registered_session_id != session_id:
            return
        try:
            async with httpx.AsyncClient(
                base_url=self.core_url,
                timeout=5.0,
                transport=self.core_transport,
            ) as client:
                response = await client.delete(f"/v1/eve-sessions/{session_id}")
                response.raise_for_status()
        except httpx.HTTPError:
            pass
        finally:
            if self._registered_session_id == session_id:
                self._registered_session_id = None

    @staticmethod
    def _message(context: TurnContext) -> str:
        # Encode angle brackets so a stored value cannot close the context tag.
        payload = json.dumps(
            {
                "reply_language": context.language,
                "relevant_memories": list(context.memories),
                "physical_action_results": list(context.action_results),
            },
            ensure_ascii=False,
        ).replace("<", "\\u003c").replace(">", "\\u003e")
        language_requirement = (
            "Application requirement: answer this turn using Japanese only. "
            "Switch immediately even if earlier turns were English."
            if context.language == "ja"
            else "Application requirement: answer this turn using English only. "
            "Switch immediately even if earlier turns were Japanese."
        )
        memory_requirement = ""
        if context.memories:
            memory_requirement = (
                "\nApplication requirement: relevant memory is supplied for this turn. "
                "Use it in a complete natural sentence that says the fact belongs to "
                "the user; do not answer with only the remembered value."
            )
        return (
            f"{language_requirement}{memory_requirement}\n\n"
            "<stackchan_turn_context_json>\n"
            f"{payload}\n"
            "</stackchan_turn_context_json>\n\n"
            f"{context.transcript}"
        )

    async def _post_message(self, client: httpx.AsyncClient, message: str) -> None:
        path = (
            "/eve/v1/session"
            if self.session_id is None
            else f"/eve/v1/session/{self.session_id}"
        )
        response = await client.post(path, json={"message": message})
        response.raise_for_status()
        payload = response.json()
        if not payload.get("ok"):
            raise RuntimeError(f"Eve rejected the turn: {payload}")
        returned_session_id = str(payload.get("sessionId", ""))
        if not returned_session_id:
            raise RuntimeError("Eve did not return a session ID")
        if self.session_id is not None and returned_session_id != self.session_id:
            raise RuntimeError("Eve changed the durable session ID during a follow-up")
        self.session_id = returned_session_id
        await self._register_device_binding(returned_session_id)
        self._waiting = False

    async def _submit_input_response(
        self,
        client: httpx.AsyncClient,
        approval: _EveToolApproval,
        *,
        approved: bool,
    ) -> None:
        if self.session_id != approval.session_id:
            raise RuntimeError("stale or cross-session Eve approval response rejected")
        response = await client.post(
            f"/eve/v1/session/{approval.session_id}",
            json={
                "inputResponses": [
                    {
                        "requestId": approval.request_id,
                        "optionId": "approve" if approved else "deny",
                    }
                ]
            },
        )
        response.raise_for_status()
        payload = response.json()
        if not payload.get("ok") or payload.get("sessionId") != approval.session_id:
            raise RuntimeError(f"Eve rejected the approval response: {payload}")
        self._waiting = False

    async def _resolve_active_approval(
        self,
        client: httpx.AsyncClient,
        approval: _EveToolApproval,
        *,
        approved: bool,
    ) -> None:
        if self.session_id != approval.session_id or self._pending_approval != approval:
            raise RuntimeError("stale or cross-session Eve approval response rejected")
        now = time.monotonic()
        if approved and now >= approval.expires_at:
            approved = False
        self._pending_approval = None
        if not approved:
            self._pending_denial = approval
        try:
            await self._submit_input_response(client, approval, approved=approved)
        except (httpx.HTTPError, RuntimeError):
            if approved and time.monotonic() < approval.expires_at:
                self._pending_approval = approval
                self._schedule_approval_timeout(approval)
            else:
                self._pending_denial = approval
            raise
        else:
            self._pending_denial = None

    def _approval_prompt(self, approval: _EveToolApproval, language: str) -> str:
        label = _safe_tool_label(approval.tool_name)
        if language == "ja":
            return (
                f"{label}、{approval.action_summary}の実行を許可しますか？ "
                "承認するには、画面に表示された2桁の番号を承認の後に言ってください。"
                "拒否する場合は拒否と言ってください。"
            )
        return (
            f"Allow the {label} action for {approval.action_summary}? "
            "To approve, say approve followed by the two-digit number on my screen, "
            "or say deny."
        )

    def _approval_reprompt(self, language: str) -> str:
        if language == "ja":
            return (
                "その言葉は許可として扱いませんでした。"
                "承認の後に画面の2桁の番号を言うか、拒否と言ってください。"
            )
        return (
            "I did not treat that as permission. "
            "Say approve followed by the two-digit number on my screen, or deny."
        )

    def _capture_approval(self, data: dict[str, Any]) -> _EveToolApproval:
        requests = data.get("requests")
        if not isinstance(requests, list) or len(requests) != 1:
            raise RuntimeError("voice approval requires exactly one pending Eve request")
        request = requests[0]
        if not isinstance(request, dict) or request.get("kind") != "tool-approval":
            raise RuntimeError("unsupported Eve input request for the voice channel")
        action = request.get("action")
        request_id = request.get("requestId")
        if (
            not isinstance(action, dict)
            or action.get("kind") != "tool-call"
            or not isinstance(action.get("toolName"), str)
            or not action["toolName"]
            or not isinstance(request_id, str)
            or not request_id
            or self.session_id is None
        ):
            raise RuntimeError("malformed Eve tool approval request")
        option_ids = {
            option.get("id")
            for option in request.get("options", [])
            if isinstance(option, dict)
        }
        if not {"approve", "deny"}.issubset(option_ids):
            raise RuntimeError("Eve tool approval is missing approve/deny options")
        tool_name = action["toolName"]
        action_summary = _voice_safe_action_summary(
            action.get("input"), self.approval_summary_fields.get(tool_name)
        )
        if action_summary is None:
            raise RuntimeError("Eve tool approval lacks a voice-safe action summary")
        challenge = _display_only_challenge(
            _safe_tool_label(tool_name), action_summary
        )
        approval = _EveToolApproval(
            request_id=request_id,
            session_id=self.session_id,
            tool_name=tool_name,
            action_summary=action_summary,
            challenge=challenge,
            expires_at=time.monotonic() + self.approval_timeout_seconds,
        )
        if self._pending_approval not in {None, approval}:
            raise RuntimeError("a different Eve tool approval is already pending")
        self._pending_approval = approval
        self._schedule_approval_timeout(approval)
        return approval

    def _schedule_approval_timeout(self, approval: _EveToolApproval) -> None:
        task = self._approval_timeout_task
        if task is not None and not task.done():
            task.cancel()
        self._approval_timeout_task = asyncio.create_task(
            self._deny_approval_after_timeout(approval)
        )

    async def _deny_approval_after_timeout(self, approval: _EveToolApproval) -> None:
        try:
            await asyncio.sleep(max(0.0, approval.expires_at - time.monotonic()))
            async with self._approval_response_lock:
                if self._pending_approval != approval:
                    return
                # Expiration is a local security boundary. Mark the request
                # irrevocably non-approvable before attempting network I/O.
                self._pending_approval = None
                self._pending_denial = approval
                timeout = httpx.Timeout(self.timeout_seconds, connect=5.0)
                async with httpx.AsyncClient(
                    base_url=self.base_url, timeout=timeout, transport=self.transport
                ) as client:
                    await self._submit_input_response(client, approval, approved=False)
                if self._pending_denial == approval:
                    self._pending_denial = None
        except (asyncio.CancelledError, httpx.HTTPError, RuntimeError):
            return

    def pending_tool_approval(self) -> PendingToolApproval | None:
        approval = self._pending_approval
        if approval is None:
            return None
        return PendingToolApproval(
            request_id=approval.request_id,
            tool_name=approval.tool_name,
            action_summary=approval.action_summary,
            challenge=approval.challenge,
            seconds_remaining=max(0.0, approval.expires_at - time.monotonic()),
        )

    def blocks_normal_turn(self) -> bool:
        return self._pending_approval is not None or self._pending_denial is not None

    async def _post_message_safely(
        self, client: httpx.AsyncClient, message: str
    ) -> None:
        """Capture an accepted session even if local turn cancellation races the POST."""
        self._submitting = True
        task = asyncio.create_task(self._post_message(client, message))
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            try:
                await task
            except BaseException:
                self._cancel_pending = False
                raise
            raise
        except BaseException:
            self._cancel_pending = False
            raise
        finally:
            self._submitting = False

    def _turn_started(self, turn_id: str) -> None:
        self._active_turn_id = turn_id
        if not self._cancel_pending or self.session_id is None:
            return
        self._cancel_pending = False
        self._schedule_cancel(self.session_id, turn_id)

    def _retire_local_session(self) -> None:
        self.session_id = None
        self._cursor = 0
        self._waiting = True
        self._active_turn_id = None
        self._cancel_pending = False

    async def _drain_pending_turn(self, client: httpx.AsyncClient) -> None:
        """Reach the abandoned turn boundary before accepting a new message."""
        assert self.session_id is not None
        stream_path = f"/eve/v1/session/{self.session_id}/stream"
        reconnects = 0
        while reconnects <= 3:
            try:
                async with client.stream(
                    "GET", stream_path, params={"startIndex": self._cursor}
                ) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if not line:
                            continue
                        event: dict[str, Any] = json.loads(line)
                        self._cursor += 1
                        event_type = str(event.get("type", ""))
                        data = event.get("data")
                        data = data if isinstance(data, dict) else {}
                        turn_id = data.get("turnId")
                        if event_type == "turn.started" and isinstance(turn_id, str):
                            self._turn_started(turn_id)
                        if event_type == "session.failed":
                            await self._unregister_device_binding(self.session_id)
                            self._retire_local_session()
                            raise RuntimeError("Eve session failed while draining a turn")
                        if event_type == "session.waiting":
                            self._waiting = True
                            self._active_turn_id = None
                            return
            except httpx.TransportError:
                pass
            reconnects += 1
            if reconnects <= 3:
                await asyncio.sleep(0.05 * reconnects)
        raise RuntimeError("Eve pending turn did not reach its session boundary")

    async def generate(self, context: TurnContext) -> AsyncIterator[str]:
        timeout = httpx.Timeout(self.timeout_seconds, connect=5.0)
        async with httpx.AsyncClient(
            base_url=self.base_url, timeout=timeout, transport=self.transport
        ) as client:
            # A failed timeout denial must be retried before the abandoned Eve
            # turn can be drained. All other unfinished turns are drained first.
            if (
                self.session_id is not None
                and not self._waiting
                and self._pending_denial is None
            ):
                await self._drain_pending_turn(client)
            denial_recovered = False
            approval_resolved = False
            async with self._approval_response_lock:
                pending_approval = self._pending_approval
                pending_denial = self._pending_denial
                if pending_denial is not None:
                    await self._submit_input_response(client, pending_denial, approved=False)
                    if self._pending_denial == pending_denial:
                        self._pending_denial = None
                    denial_recovered = True
                elif pending_approval is not None:
                    decision = classify_voice_approval(
                        context.transcript, challenge=pending_approval.challenge
                    )
                    if decision is None:
                        yield self._approval_reprompt(context.language)
                        return
                    timeout_task = self._approval_timeout_task
                    if timeout_task is not None and not timeout_task.done():
                        timeout_task.cancel()
                    await self._resolve_active_approval(
                        client, pending_approval, approved=decision
                    )
                    approval_resolved = True
            if denial_recovered:
                # The user's current utterance is not an approval response; it
                # arrived while a prior fail-closed denial was being recovered.
                # Drain that denial continuation, then submit this utterance as
                # the next normal turn instead of silently dropping it.
                if not self._waiting:
                    await self._drain_pending_turn(client)
                await self._post_message_safely(client, self._message(context))
            elif not approval_resolved:
                # The timeout task may have completed a denial while this turn
                # was waiting for the response lock. Re-check the durable state
                # after the lock so the denial continuation cannot be mistaken
                # for the answer to this new user message.
                if self.session_id is not None and not self._waiting:
                    await self._drain_pending_turn(client)
                await self._post_message_safely(client, self._message(context))
            assert self.session_id is not None
            stream_path = f"/eve/v1/session/{self.session_id}/stream"
            emitted = ""
            bounded = False
            reconnects = 0
            current_turn_started = False
            turn_error: RuntimeError | None = None
            max_sentences = response_sentence_budget(context)
            while reconnects <= 3:
                try:
                    async with client.stream(
                        "GET", stream_path, params={"startIndex": self._cursor}
                    ) as response:
                        response.raise_for_status()
                        async for line in response.aiter_lines():
                            if not line:
                                continue
                            event: dict[str, Any] = json.loads(line)
                            self._cursor += 1
                            event_type = str(event.get("type", ""))
                            data = event.get("data")
                            data = data if isinstance(data, dict) else {}
                            turn_id = data.get("turnId")
                            if event_type == "turn.started":
                                current_turn_started = True
                                if isinstance(turn_id, str):
                                    self._turn_started(turn_id)
                            if event_type == "input.requested":
                                approval = self._capture_approval(data)
                                yield self._approval_prompt(approval, context.language)
                            if event_type == "message.appended":
                                delta = data.get("messageDelta")
                                if isinstance(delta, str) and delta and not bounded:
                                    piece, reached_boundary = bound_spoken_delta(
                                        emitted,
                                        clean_spoken_delta(delta),
                                        context.language,
                                        max_sentences=max_sentences,
                                    )
                                    if piece:
                                        emitted += piece
                                        yield piece
                                    bounded = bounded or reached_boundary
                            elif event_type == "turn.failed":
                                message = str(data.get("message") or event_type)
                                turn_error = RuntimeError(
                                    f"Eve intelligence failure: {message}"
                                )
                            elif event_type == "session.failed":
                                message = str(data.get("message") or event_type)
                                await self._unregister_device_binding(self.session_id)
                                self._retire_local_session()
                                raise RuntimeError(f"Eve intelligence failure: {message}")
                            elif event_type == "turn.cancelled":
                                # Drain through session.waiting so a follow-up
                                # never consumes this turn's stale boundary.
                                continue
                            elif event_type == "session.waiting":
                                if not current_turn_started:
                                    # A producer may have been locally cancelled
                                    # before it consumed the previous turn's tail.
                                    continue
                                self._waiting = True
                                self._active_turn_id = None
                                if turn_error is not None:
                                    raise turn_error
                                return
                except httpx.TransportError:
                    pass
                reconnects += 1
                if reconnects <= 3:
                    await asyncio.sleep(0.05 * reconnects)
            raise RuntimeError("Eve event stream ended before the session became ready")

    def cancel(self) -> None:
        session_id = self.session_id
        turn_id = self._active_turn_id
        if session_id is not None and turn_id is not None:
            self._cancel_pending = False
            self._schedule_cancel(session_id, turn_id)
            return
        if self._submitting or (session_id is not None and not self._waiting):
            self._cancel_pending = True

    def _schedule_cancel(self, session_id: str, turn_id: str) -> None:
        task = asyncio.create_task(self._cancel_active_turn(session_id, turn_id))
        self._cancel_tasks.add(task)
        task.add_done_callback(self._cancel_tasks.discard)

    async def _cancel_active_turn(self, session_id: str, turn_id: str) -> None:
        try:
            async with httpx.AsyncClient(
                base_url=self.base_url, timeout=5.0, transport=self.transport
            ) as client:
                response = await client.post(
                    f"/eve/v1/session/{session_id}/cancel", json={"turnId": turn_id}
                )
                response.raise_for_status()
        except httpx.HTTPError:
            # The Python producer is already cancelled locally. A failed remote
            # cancellation must not break audio teardown or the device socket.
            return

    async def aclose(self) -> None:
        timeout_task = self._approval_timeout_task
        if timeout_task is not None and not timeout_task.done():
            timeout_task.cancel()
            await asyncio.gather(timeout_task, return_exceptions=True)
        if self._cancel_tasks:
            await asyncio.gather(*tuple(self._cancel_tasks), return_exceptions=True)
        session_id = self.session_id
        registered_session_id = self._registered_session_id
        if registered_session_id is not None:
            await self._unregister_device_binding(registered_session_id)
        if session_id is None:
            return
        try:
            async with httpx.AsyncClient(
                base_url=self.base_url, timeout=5.0, transport=self.transport
            ) as client:
                approval = self._pending_approval or self._pending_denial
                if approval is not None and approval.session_id == session_id:
                    try:
                        await self._submit_input_response(client, approval, approved=False)
                    except (httpx.HTTPError, RuntimeError):
                        pass
                response = await client.post(
                    f"/eve/v1/session/{session_id}/reset",
                    json={"reason": "Stack-chan device connection closed"},
                )
                response.raise_for_status()
        except httpx.HTTPError:
            return
        finally:
            self._pending_approval = None
            self._pending_denial = None
            self._retire_local_session()
