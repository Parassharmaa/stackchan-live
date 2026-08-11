import re
import sqlite3
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class Memory:
    id: int
    content: str
    language: str
    kind: str
    importance: float
    created_at: float
    updated_at: float
    expires_at: float | None = None
    memory_key: str | None = None


@dataclass(frozen=True, slots=True)
class ProfileMemoryCandidate:
    key: str
    content: str
    language: str
    importance: float = 0.72


class SensitiveMemoryError(ValueError):
    """Raised when a memory belongs to a category we never persist."""

    def __init__(self, category: str) -> None:
        self.category = category
        super().__init__(f"refusing to store sensitive {category} data")


_SENSITIVE_MEMORY_PATTERNS = {
    "credential": (
        re.compile(
            r"\b(?:password|passcode|pin|api[ -]?key|access[ -]?token|"
            r"auth(?:entication)?[ -]?token|private[ -]?key|secret)\b",
            re.IGNORECASE,
        ),
        re.compile(r"(?:パスワード|暗証番号|APIキー|アクセストークン|認証トークン|秘密鍵)"),
    ),
    "financial": (
        re.compile(
            r"\b(?:cvv|cvc|card[ -]?number|credit[ -]?card|debit[ -]?card|"
            r"bank[ -]?account|routing[ -]?number|iban)\b",
            re.IGNORECASE,
        ),
        re.compile(r"(?:カード番号|クレジットカード|デビットカード|銀行口座|口座番号|暗証番号)"),
    ),
    "health": (
        re.compile(
            r"\b(?:diabetes|cancer|pregnan(?:t|cy)|hypertension|high[ -]?blood[ -]?"
            r"pressure|asthma|depression|anxiety|epilepsy|hiv|aids|stroke|"
            r"metformin|insulin|chemotherapy|diagnos(?:is|ed)|medical[ -]?condition|"
            r"medication|prescription|blood[ -]?type|allerg(?:y|ic))\b",
            re.IGNORECASE,
        ),
        re.compile(
            r"(?:糖尿病|がん|癌|妊娠|高血圧|喘息|うつ病|鬱病|不安障害|てんかん|"
            r"メトホルミン|インスリン|診断|持病|病気|病歴|服薬|薬を飲|処方薬|"
            r"血液型|アレルギー)"
        ),
    ),
}


def validate_memory_content(content: str) -> str:
    """Normalize a memory and reject sensitive categories before any DB lookup/write."""
    normalized = " ".join(content.strip().split())
    for category, patterns in _SENSITIVE_MEMORY_PATTERNS.items():
        if any(pattern.search(normalized) for pattern in patterns):
            raise SensitiveMemoryError(category)
    digits = re.sub(r"[^0-9]", "", normalized)
    if 13 <= len(digits) <= 19 and re.search(r"(?:\d[ -]?){13,19}", normalized):
        raise SensitiveMemoryError("financial")
    return normalized


def _japanese_ngrams(text: str, size: int = 2) -> set[str]:
    normalized = "".join(
        character.casefold()
        for character in unicodedata.normalize("NFKC", text)
        if not character.isspace() and not unicodedata.category(character).startswith("P")
    )
    return {
        normalized[index : index + size]
        for index in range(max(0, len(normalized) - size + 1))
    }


def _has_japanese(text: str) -> bool:
    return bool(re.search(r"[\u3040-\u30ff\u3400-\u9fff]", text))


def _memory_key(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return re.sub(r"[^\w\u3040-\u30ff\u3400-\u9fff]+", "_", normalized).strip("_")


def _clean_profile_value(value: str) -> str:
    return " ".join(value.strip(" \t\r\n、,。.！!?？").split())


def _stable_profile_statement(text: str) -> bool:
    normalized = unicodedata.normalize("NFKC", text).casefold()
    transient = (
        "today",
        "right now",
        "at the moment",
        "for now",
        "this time",
        "今日",
        "今だけ",
        "今のところ",
        "今回は",
    )
    return not any(cue in normalized for cue in transient)


def _is_low_value_episode(transcript: str, response: str, language: str) -> bool:
    """Reject acknowledgements, noise captions, and circular memory chatter."""
    user = unicodedata.normalize("NFKC", transcript).casefold().strip()
    assistant = unicodedata.normalize("NFKC", response).casefold().strip()
    if re.fullmatch(r"[\s\W_]*(?:\([^)]*\)|\[[^]]*\]|\*[^*]*\*)[\s\W_]*", user):
        return True
    filler = {
        "thanks", "thank you", "okay", "ok", "yes", "no", "sure", "hello", "hi",
        "you're welcome", "you are welcome", "そうですね", "ありがとう", "はい", "うん",
        "わかりました", "了解", "こんにちは", "スタックちゃん", "すたっくちゃん",
    }
    normalized_user = re.sub(r"[\s,，.。!！?？、]+", " ", user).strip()
    normalized_assistant = re.sub(r"[\s,，.。!！?？、]+", " ", assistant).strip()
    if normalized_user in filler or normalized_assistant in filler:
        return True
    memory_chatter = (
        "your favorite", "you remember", "remembered conversation", "memory", "favorite drink",
        "あなたが覚え", "覚えてくれた", "会話記録", "好きな飲み物", "記憶",
    )
    if any(cue in assistant for cue in memory_chatter):
        return True
    minimum_words = 4 if language != "ja" else 1
    if language != "ja" and len(re.findall(r"[a-z0-9]+", user)) < minimum_words:
        return True
    return False


def extract_profile_memories(
    transcript: str, language: str
) -> list[ProfileMemoryCandidate]:
    """Extract only direct, stable, non-sensitive profile declarations.

    These rules intentionally ignore inference, questions, vague pronouns, and
    transient preferences. Sensitive validation still runs at the storage
    boundary, so a new pattern cannot bypass the global denylist.
    """
    text = " ".join(transcript.strip().split())
    if not text or not _stable_profile_statement(text):
        return []
    if text.rstrip().endswith(("?", "？")) or re.search(
        r"(?:何|どれ|どの|誰|いつ|どこ|どう).*(?:ですか|ますか|だっけ)[。.?？]?$",
        text,
    ):
        return []
    candidates: list[ProfileMemoryCandidate] = []
    vague = {"it", "this", "that", "them", "you", "これ", "それ", "あれ"}

    if language == "ja":
        favorite = re.search(
            r"(?:私|わたし|僕|ぼく)の好きな([^、。！？]{1,16}?)は"
            r"([^、。！？]{1,60}?)(?:です|だ)?(?:[。！？]|$)",
            text,
        )
        if favorite:
            category = _clean_profile_value(favorite.group(1))
            value = _clean_profile_value(favorite.group(2))
            if category and value and value not in vague:
                candidates.append(
                    ProfileMemoryCandidate(
                        f"favorite:{_memory_key(category)}",
                        f"ユーザーの好きな{category}は{value}です。",
                        "ja",
                        0.78,
                    )
                )
        preferred_name = re.search(
            r"^(?:(?:私|わたし|僕|ぼく)を)?([^、。！？]{1,30}?)と呼んで",
            text,
        )
        if not preferred_name:
            preferred_name = re.search(
                r"(?:私|わたし|僕|ぼく)の名前は([^、。！？]{1,30}?)(?:です|だ)?(?:[。！？]|$)",
                text,
            )
        if preferred_name:
            name = _clean_profile_value(preferred_name.group(1))
            if name and name not in vague:
                candidates.append(
                    ProfileMemoryCandidate(
                        "identity:preferred_name",
                        f"ユーザーは{name}と呼ばれたいです。",
                        "ja",
                        0.9,
                    )
                )
        preference = re.search(
            r"(?:私|わたし|僕|ぼく)は?([^、。！？]{1,70}?)(?:が|は)"
            r"(大好き|好きじゃない|好きではない|好き|苦手|嫌い)"
            r"(?:です|だ|よ|んだ)?(?:[。！？]|$)",
            text,
        )
        if not preference:
            # Japanese commonly drops the first-person subject. Accept a
            # direct, single-subject correction without inferring preferences
            # from statements about someone else.
            preference = re.search(
                r"^([^はが、。！？]{1,40}?)(?:が|は)?"
                r"(好きじゃない|好きではない)(?:です|だ|よ|んだ)?(?:[。！？]|$)",
                text,
            )
        if preference:
            value = _clean_profile_value(preference.group(1))
            sentiment = preference.group(2)
            excluded_subjects = {
                "あなた", "君", "きみ", "スタックちゃん", "すたっくちゃん", "stack-chan"
            }
            if value and value not in vague and value not in excluded_subjects:
                positive = sentiment in {"好き", "大好き"}
                candidates.append(
                    ProfileMemoryCandidate(
                        f"preference:{_memory_key(value)}",
                        (
                            f"ユーザーは{value}が好きです。"
                            if positive
                            else f"ユーザーは{value}が苦手です。"
                        ),
                        "ja",
                    )
                )
    else:
        favorite = re.search(
            r"\bmy favou?rite\s+([a-z][a-z0-9 _-]{0,30}?)\s+is\s+"
            r"([^.!?]{1,100})(?:[.!?]|$)",
            text,
            flags=re.IGNORECASE,
        )
        if favorite:
            category = _clean_profile_value(favorite.group(1)).casefold()
            value = _clean_profile_value(favorite.group(2))
            if category and value and value.casefold() not in vague:
                candidates.append(
                    ProfileMemoryCandidate(
                        f"favorite:{_memory_key(category)}",
                        f"The user's favorite {category} is {value}.",
                        "en",
                        0.78,
                    )
                )
        preferred_name = re.search(
            r"\b(?:please\s+)?call me\s+([^.!?]{1,40})(?:[.!?]|$)",
            text,
            flags=re.IGNORECASE,
        )
        if not preferred_name:
            preferred_name = re.search(
                r"\bmy name is\s+([^.!?]{1,40})(?:[.!?]|$)",
                text,
                flags=re.IGNORECASE,
            )
        if preferred_name:
            name = _clean_profile_value(preferred_name.group(1))
            if name and name.casefold() not in vague:
                candidates.append(
                    ProfileMemoryCandidate(
                        "identity:preferred_name",
                        f"The user prefers to be called {name}.",
                        "en",
                        0.9,
                    )
                )
        preference = re.search(
            r"\bI\s+(like|love|enjoy|prefer|dislike|hate|don't like|do not like)\s+"
            r"([^.!?]{1,120})(?:[.!?]|$)",
            text,
            flags=re.IGNORECASE,
        )
        if preference:
            sentiment = preference.group(1).casefold()
            value = _clean_profile_value(preference.group(2))
            if value and value.casefold() not in vague:
                positive = sentiment in {"like", "love", "enjoy", "prefer"}
                verb = "likes" if positive else "dislikes"
                candidates.append(
                    ProfileMemoryCandidate(
                        f"preference:{_memory_key(value)}",
                        f"The user {verb} {value}.",
                        "en",
                    )
                )

    unique: dict[str, ProfileMemoryCandidate] = {}
    for candidate in candidates:
        unique[candidate.key] = candidate
    return list(unique.values())


def _requests_profile_summary(query: str) -> bool:
    normalized = unicodedata.normalize("NFKC", query).casefold()
    return any(
        cue in normalized
        for cue in (
            "what do you know about me",
            "what do you remember about me",
            "my preferences",
            "私について何を",
            "私のことを覚え",
            "私の好み",
        )
    )


def _requests_recent_episode(query: str) -> bool:
    normalized = unicodedata.normalize("NFKC", query).casefold()
    return any(
        cue in normalized
        for cue in (
            "what did we talk about",
            "what did we discuss",
            "earlier conversation",
            "last time we talked",
            "何を話",
            "前に話",
            "前回の話",
            "さっきの話",
        )
    )


def _wake_name_only(query: str) -> bool:
    normalized = unicodedata.normalize("NFKC", query).casefold().strip()
    normalized = re.sub(r"[\s,，.。!！?？、]+", "", normalized)
    return normalized in {
        "stackchan",
        "スタックちゃん",
        "すたっくちゃん",
        "스태크chan",
    }


def _legacy_profile_is_corrupt(memory: Memory) -> bool:
    """Hide malformed rows produced by earlier broad memory extraction."""
    normalized = unicodedata.normalize("NFKC", memory.content).casefold()
    invalid_profile_subject = memory.kind == "profile" and bool(
        re.match(
            r"^ユーザーは(?:あなた|君|きみ|スタックちゃん|すたっくちゃん|stack-chan)(?:が|は)",
            normalized,
        )
    )
    # Old explicit-memory extraction could persist an entire multi-intent turn,
    # including the wake phrase and a device command. Its shared "Stack-chan"
    # n-grams then polluted nearly every later Japanese question.
    mixed_command_memory = memory.kind in {"explicit", "fact"} and bool(
        re.search(
            r"(?:左|右|上|下).{0,8}(?:向いて|見て)|"
            r"(?:turn|look).{0,12}(?:left|right|up|down)",
            normalized,
        )
        and re.search(r"(?:好き|嫌い|苦手|\blike\b|\bdislike\b|\blove\b|\bhate\b)", normalized)
    )
    return invalid_profile_subject or mixed_command_memory


def _requests_preferred_name(query: str) -> bool:
    normalized = unicodedata.normalize("NFKC", query).casefold()
    return bool(
        re.search(r"\b(?:do you know|do you remember|what(?:'s| is)) my name\b", normalized)
        or re.search(r"(?:私|わたし|僕|ぼく)の名前|名前(?:を)?(?:知って|覚えて)", normalized)
    )


_ENGLISH_QUERY_STOPWORDS = {
    "a",
    "about",
    "actually",
    "am",
    "an",
    "and",
    "are",
    "can",
    "could",
    "do",
    "for",
    "from",
    "how",
    "i",
    "in",
    "is",
    "it",
    "like",
    "me",
    "my",
    "of",
    "on",
    "please",
    "some",
    "tell",
    "that",
    "the",
    "then",
    "this",
    "to",
    "very",
    "what",
    "when",
    "where",
    "which",
    "who",
    "why",
    "would",
    "you",
    "your",
}


def _lexical_query_terms(query: str) -> list[str]:
    normalized = unicodedata.normalize("NFKC", query).casefold()
    if _has_japanese(normalized):
        return [term for term in normalized.replace("\u3000", " ").split() if term]
    return [
        term
        for term in re.findall(r"[a-z0-9]+(?:['-][a-z0-9]+)?", normalized)
        if len(term) >= 2 and term not in _ENGLISH_QUERY_STOPWORDS
    ]


class MemoryStore:
    def __init__(
        self,
        path: Path,
        *,
        automatic_profiles: bool = True,
        episodic_memory: bool = True,
        episode_retention_days: int = 30,
        episode_limit: int = 50,
    ) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.automatic_profiles = automatic_profiles
        self.episodic_memory = episodic_memory
        self.episode_retention_days = max(1, episode_retention_days)
        self.episode_limit = max(1, episode_limit)
        self._migrate()

    def _migrate(self) -> None:
        self.connection.executescript(
            """
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS memories (
                id INTEGER PRIMARY KEY,
                content TEXT NOT NULL,
                language TEXT NOT NULL DEFAULT 'und',
                kind TEXT NOT NULL DEFAULT 'fact',
                importance REAL NOT NULL DEFAULT 0.5 CHECK (importance BETWEEN 0 AND 1),
                created_at REAL NOT NULL,
                last_accessed_at REAL NOT NULL,
                access_count INTEGER NOT NULL DEFAULT 0
            );
            CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
                content, content='memories', content_rowid='id', tokenize='unicode61'
            );
            CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
              INSERT INTO memories_fts(rowid, content) VALUES (new.id, new.content);
            END;
            CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
              INSERT INTO memories_fts(memories_fts, rowid, content)
              VALUES ('delete', old.id, old.content);
            END;
            CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE ON memories BEGIN
              INSERT INTO memories_fts(memories_fts, rowid, content)
              VALUES ('delete', old.id, old.content);
              INSERT INTO memories_fts(rowid, content) VALUES (new.id, new.content);
            END;
            """
        )
        columns = {
            row["name"]
            for row in self.connection.execute("PRAGMA table_info(memories)").fetchall()
        }
        if "updated_at" not in columns:
            self.connection.execute("ALTER TABLE memories ADD COLUMN updated_at REAL")
        if "expires_at" not in columns:
            self.connection.execute("ALTER TABLE memories ADD COLUMN expires_at REAL")
        if "memory_key" not in columns:
            self.connection.execute("ALTER TABLE memories ADD COLUMN memory_key TEXT")
        # A pre-FTS database can already contain rows when the external-content
        # index and its update trigger are first created. Populate the index
        # before the metadata backfill fires that trigger; deleting a missing
        # FTS row otherwise reports a misleading "database is malformed".
        self.connection.execute(
            "INSERT INTO memories_fts(memories_fts) VALUES ('rebuild')"
        )
        self.connection.execute(
            "UPDATE memories SET updated_at=created_at WHERE updated_at IS NULL"
        )
        self.connection.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS memories_memory_key "
            "ON memories(memory_key) WHERE memory_key IS NOT NULL"
        )
        self.connection.execute(
            "CREATE INDEX IF NOT EXISTS memories_kind_created "
            "ON memories(kind, created_at DESC)"
        )
        self.connection.commit()
        self.prune_expired()

    @staticmethod
    def _from_row(row: sqlite3.Row) -> Memory:
        return Memory(
            id=row["id"],
            content=row["content"],
            language=row["language"],
            kind=row["kind"],
            importance=row["importance"],
            created_at=row["created_at"],
            updated_at=row["updated_at"] or row["created_at"],
            expires_at=row["expires_at"],
            memory_key=row["memory_key"],
        )

    def remember(
        self,
        content: str,
        *,
        language: str = "und",
        kind: str = "fact",
        importance: float = 0.5,
        expires_at: float | None = None,
        memory_key: str | None = None,
    ) -> Memory:
        normalized = validate_memory_content(content)
        now = time.time()
        cursor = self.connection.execute(
            """INSERT INTO memories(
                   content, language, kind, importance, created_at, last_accessed_at,
                   updated_at, expires_at, memory_key
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                normalized,
                language,
                kind,
                importance,
                now,
                now,
                now,
                expires_at,
                memory_key,
            ),
        )
        self.connection.commit()
        row = self.connection.execute(
            "SELECT * FROM memories WHERE id = ?", (cursor.lastrowid,)
        ).fetchone()
        assert row is not None
        return self._from_row(row)

    def remember_once(
        self, content: str, *, language: str = "und", kind: str = "fact", importance: float = 0.5
    ) -> tuple[Memory, bool]:
        normalized = validate_memory_content(content)
        row = self.connection.execute(
            """SELECT * FROM memories
               WHERE content = ? AND (expires_at IS NULL OR expires_at > ?)
               ORDER BY id DESC LIMIT 1""",
            (normalized, time.time()),
        ).fetchone()
        if row:
            if kind == "explicit" and row["kind"] != "explicit":
                now = time.time()
                self.connection.execute(
                    """UPDATE memories
                       SET kind='explicit', importance=MAX(importance, ?),
                           updated_at=?, expires_at=NULL, memory_key=NULL
                       WHERE id=?""",
                    (importance, now, row["id"]),
                )
                self.connection.commit()
                row = self.connection.execute(
                    "SELECT * FROM memories WHERE id=?", (row["id"],)
                ).fetchone()
                assert row is not None
            return self._from_row(row), False
        return self.remember(
            normalized, language=language, kind=kind, importance=importance
        ), True

    def upsert_profile(self, candidate: ProfileMemoryCandidate) -> tuple[Memory, bool]:
        normalized = validate_memory_content(candidate.content)
        now = time.time()
        existing = self.connection.execute(
            "SELECT * FROM memories WHERE memory_key=?", (candidate.key,)
        ).fetchone()
        if existing is None:
            return (
                self.remember(
                    normalized,
                    language=candidate.language,
                    kind="profile",
                    importance=candidate.importance,
                    memory_key=candidate.key,
                ),
                True,
            )
        changed = (
            existing["content"] != normalized
            or existing["language"] != candidate.language
            or existing["kind"] != "profile"
        )
        self.connection.execute(
            """UPDATE memories
               SET content=?, language=?, kind='profile', importance=?,
                   updated_at=?, last_accessed_at=?, expires_at=NULL
               WHERE id=?""",
            (
                normalized,
                candidate.language,
                candidate.importance,
                now,
                now,
                existing["id"],
            ),
        )
        self.connection.commit()
        row = self.connection.execute(
            "SELECT * FROM memories WHERE id=?", (existing["id"],)
        ).fetchone()
        assert row is not None
        return self._from_row(row), changed

    def record_episode(self, transcript: str, response: str, language: str) -> Memory:
        now = time.time()
        user = " ".join(transcript.strip().split())[:240]
        assistant = " ".join(response.strip().split())[:320]
        if language == "ja":
            content = f"会話記録。ユーザー: {user} スタックちゃん: {assistant}"
        else:
            content = f"Conversation episode. User: {user} Stack-chan: {assistant}"
        item = self.remember(
            content,
            language=language,
            kind="episode",
            importance=0.35,
            expires_at=now + self.episode_retention_days * 86_400,
        )
        self.connection.execute(
            """DELETE FROM memories WHERE id IN (
                   SELECT id FROM memories WHERE kind='episode'
                   ORDER BY created_at DESC, id DESC LIMIT -1 OFFSET ?
               )""",
            (self.episode_limit,),
        )
        self.connection.commit()
        return item

    def capture_profile_memories(self, transcript: str, language: str) -> list[Memory]:
        """Persist stable profile declarations as soon as final STT is available."""
        if extract_explicit_memory(transcript, language):
            return []
        captured: list[Memory] = []
        if self.automatic_profiles:
            for candidate in extract_profile_memories(transcript, language):
                try:
                    item, _ = self.upsert_profile(candidate)
                except SensitiveMemoryError:
                    continue
                captured.append(item)
        return captured

    def capture_episode_memory(
        self, transcript: str, response: str, language: str
    ) -> list[Memory]:
        """Persist one completed non-profile conversation with bounded retention."""
        if not self.episodic_memory or extract_explicit_memory(transcript, language):
            return []
        if extract_profile_memories(transcript, language):
            return []
        normalized = unicodedata.normalize("NFKC", transcript).casefold()
        memory_recall = (
            _requests_profile_summary(transcript)
            or _requests_recent_episode(transcript)
            or any(
                cue in normalized
                for cue in (
                    "what is my favorite",
                    "what's my favorite",
                    "私の好きな",
                    "私のお気に入り",
                )
            )
        )
        command_cues = (
            "remember",
            "forget",
            "delete memory",
            "move your head",
            "turn your head",
            "set the light",
            "play music",
            "dance",
            "count ",
            "覚え",
            "忘れて",
            "記憶を削除",
            "頭を",
            "ライト",
            "音楽",
            "踊って",
            "数えて",
        )
        minimum_length = 6 if language == "ja" else 12
        if (
            len(transcript.strip()) < minimum_length
            or len(response.strip()) < minimum_length
            or _is_low_value_episode(transcript, response, language)
            or memory_recall
            or any(cue in normalized for cue in command_cues)
        ):
            return []
        try:
            return [self.record_episode(transcript, response, language)]
        except SensitiveMemoryError:
            return []

    def capture_automatic_memories(
        self, transcript: str, response: str, language: str
    ) -> list[Memory]:
        """Capture a stable profile or one completed bounded conversation episode."""
        profiles = self.capture_profile_memories(transcript, language)
        if profiles:
            return profiles
        return self.capture_episode_memory(transcript, response, language)

    def prune_expired(self, *, now: float | None = None) -> int:
        timestamp = time.time() if now is None else now
        cursor = self.connection.execute(
            "DELETE FROM memories WHERE expires_at IS NOT NULL AND expires_at <= ?",
            (timestamp,),
        )
        self.connection.commit()
        return cursor.rowcount

    def retrieve(self, query: str, *, limit: int = 6) -> list[Memory]:
        self.prune_expired()
        if _wake_name_only(query):
            return []
        preferred_language = "ja" if _has_japanese(query) else "en"
        if _requests_preferred_name(query):
            row = self.connection.execute(
                """SELECT * FROM memories
                   WHERE memory_key='identity:preferred_name'
                     AND (expires_at IS NULL OR expires_at > ?)
                   ORDER BY updated_at DESC LIMIT 1""",
                (time.time(),),
            ).fetchone()
            return [self._from_row(row)] if row is not None else []
        if _requests_profile_summary(query):
            rows = self.connection.execute(
                """SELECT * FROM memories
                   WHERE kind IN ('profile', 'explicit', 'fact')
                     AND (expires_at IS NULL OR expires_at > ?)
                   ORDER BY CASE WHEN language=? THEN 0 ELSE 1 END,
                            importance DESC, updated_at DESC LIMIT ?""",
                (time.time(), preferred_language, limit),
            ).fetchall()
            return [
                memory
                for row in rows
                if not _legacy_profile_is_corrupt(memory := self._from_row(row))
            ]
        if _requests_recent_episode(query):
            rows = self.connection.execute(
                """SELECT * FROM memories
                   WHERE kind='episode' AND (expires_at IS NULL OR expires_at > ?)
                   ORDER BY CASE WHEN language=? THEN 0 ELSE 1 END,
                            created_at DESC, id DESC LIMIT ?""",
                (time.time(), preferred_language, limit),
            ).fetchall()
            return [self._from_row(row) for row in rows]
        terms = _lexical_query_terms(query)
        if not terms:
            return []
        match_query = " OR ".join(f'"{term.replace(chr(34), chr(34) * 2)}"' for term in terms)
        rows = self.connection.execute(
            """
            SELECT m.*, bm25(memories_fts) AS lexical_score
            FROM memories_fts
            JOIN memories m ON m.id = memories_fts.rowid
            WHERE memories_fts MATCH ?
              AND m.kind != 'episode'
              AND (m.expires_at IS NULL OR m.expires_at > ?)
            ORDER BY lexical_score ASC, m.importance DESC, m.created_at DESC
            LIMIT ?
            """,
            (match_query, time.time(), limit),
        ).fetchall()
        # SQLite's unicode61 tokenizer does not segment unspaced Japanese text.
        # Preserve the fast FTS path for segmented languages and use a bounded
        # substring query when FTS has no match.
        if not rows:
            like_clauses = " OR ".join("content LIKE ?" for _ in terms)
            rows = self.connection.execute(
                f"""
                SELECT *, 0.0 AS lexical_score
                FROM memories
                WHERE ({like_clauses})
                  AND kind != 'episode'
                  AND (expires_at IS NULL OR expires_at > ?)
                ORDER BY importance DESC, created_at DESC
                LIMIT ?
                """,
                (*[f"%{term}%" for term in terms], time.time(), limit),
            ).fetchall()
        if not rows and _has_japanese(query):
            # `unicode61` does not segment unspaced Japanese, and a natural
            # question usually differs at the predicate (for example
            # `私の好きな色は何？` vs `私の好きな色は紫`). Rank a bounded set
            # of recent/important memories by character-bigram overlap so the
            # shared subject retrieves without requiring a copied substring.
            query_grams = _japanese_ngrams(query)
            candidates = self.connection.execute(
                """
                SELECT *, 0.0 AS lexical_score
                FROM memories
                WHERE kind != 'episode'
                  AND (expires_at IS NULL OR expires_at > ?)
                ORDER BY importance DESC, created_at DESC
                LIMIT ?
                """,
                (time.time(), max(100, limit * 20)),
            ).fetchall()
            minimum_overlap = max(2, min(4, len(query_grams) // 3))
            ranked = []
            for row in candidates:
                overlap = len(query_grams & _japanese_ngrams(row["content"]))
                if overlap < minimum_overlap:
                    continue
                containment = overlap / max(1, len(query_grams))
                ranked.append((containment + row["importance"] * 0.1, row))
            ranked.sort(key=lambda item: item[0], reverse=True)
            rows = [row for _, row in ranked[:limit]]
        rows = [
            row
            for row in rows
            if not _legacy_profile_is_corrupt(self._from_row(row))
        ]
        if rows:
            ids = [row["id"] for row in rows]
            placeholders = ",".join("?" for _ in ids)
            self.connection.execute(
                f"UPDATE memories SET access_count=access_count+1, last_accessed_at=? "
                f"WHERE id IN ({placeholders})",
                (time.time(), *ids),
            )
            self.connection.commit()
        return [self._from_row(row) for row in rows]

    def list_recent(
        self, *, limit: int = 20, include_episodes: bool = False
    ) -> list[Memory]:
        self.prune_expired()
        episode_clause = "" if include_episodes else "AND kind != 'episode'"
        rows = self.connection.execute(
            f"""SELECT * FROM memories
                WHERE (expires_at IS NULL OR expires_at > ?) {episode_clause}
                ORDER BY updated_at DESC, id DESC LIMIT ?""",
            (time.time(), max(1, min(limit, 100))),
        ).fetchall()
        return [self._from_row(row) for row in rows]

    def forget(self, memory_id: int) -> bool:
        cursor = self.connection.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
        self.connection.commit()
        return cursor.rowcount > 0

    def close(self) -> None:
        self.connection.close()


def extract_explicit_memory(transcript: str, language: str) -> str | None:
    """Extract only explicit remember requests; never infer sensitive facts."""
    text = " ".join(transcript.strip().split())
    if language == "ja":
        if re.search(r"(?:覚えないで|覚えなくて|記憶しないで)", text):
            return None
        clauses = re.findall(r"(?:^|[、。])([^、。]+(?:、[^、。]+)?)と覚えて", text)
        subject_match = re.search(
            r"((?:私|わたし|僕|俺)[^、。！？]{1,60}?)と覚えて", text
        )
        match = re.search(r"(.+?)(?:と|って)?覚えて(?:ください|おいて)?", text)
        if not match:
            return None
        candidate = (
            subject_match.group(1)
            if subject_match
            else clauses[-1]
            if clauses
            else match.group(1)
        ).strip(" 、。，.!！?")
        # Keep the nearest clause when the turn also contains other commands.
        for delimiter in ("。", "、そして", "それから"):
            if delimiter in candidate:
                candidate = candidate.rsplit(delimiter, 1)[-1].strip()
        candidate = re.sub(
            r"^(?:こんにちは|ねえ|スタック(?:ちゃん|チャン))[、,]\s*",
            "",
            candidate,
        )
        return candidate or None
    if re.search(r"\b(?:do not|don't|dont|never)\s+remember\b", text, re.IGNORECASE):
        return None
    match = re.search(
        r"\bremember(?:\s+that)?\s+(.+?)(?:[.!?]|$)", text, flags=re.IGNORECASE
    )
    return match.group(1).strip(" ,") if match else None
