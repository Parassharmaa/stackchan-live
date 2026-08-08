---
name: relevant-personal-memory
description: Retrieve and use personal memory only when it is relevant, and preserve exact identity values across Japanese and English recall.
---

# Relevant personal memory

## Retrieve memory only when the turn calls for it

When a turn could benefit from remembered personal context, Stack-chan retrieves
records whose meaning is relevant to that turn. A wake-name utterance, greeting,
or shared conversational word does not by itself justify retrieving an unrelated
preference or episode. The trajectory should expose the user utterance and the
retrieved records, so a plausible reply cannot hide irrelevant retrieval.

If the turn is ambiguous, Stack-chan responds without personalizing from a weak
match or asks what the user wants. It does not fill silence with a recurring topic
from memory.

## Preserve exact identity values across languages

When the user asks for a stored identity value such as their preferred name,
Stack-chan retrieves the canonical identity record even when the query language
differs from the record language. It reproduces the stored value exactly rather
than translating, transliterating, or guessing it. If no canonical value exists,
Stack-chan says it does not know instead of inventing one.

**Failure modes:** Retrieving coffee because a wake utterance contains
“Stack-chan”; failing an English name question because the record is Japanese;
or returning a phonetically plausible spelling that is not the stored name.
