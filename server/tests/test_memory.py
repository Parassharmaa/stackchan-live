import sqlite3
from pathlib import Path

import pytest

from stackchan_agent.memory import (
    MemoryStore,
    SensitiveMemoryError,
    extract_explicit_memory,
    extract_profile_memories,
)


def test_memory_retrieves_english_and_japanese(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory.sqlite3")
    store.remember("Paras likes strong coffee", language="en", importance=0.8)
    store.remember("パラスは東京に住んでいます", language="ja", importance=0.9)

    assert store.retrieve("coffee")[0].language == "en"
    assert store.retrieve("東京")[0].language == "ja"
    store.close()


def test_generic_like_question_does_not_retrieve_unrelated_preference(
    tmp_path: Path,
) -> None:
    store = MemoryStore(tmp_path / "memory.sqlite3")
    store.remember("I like coffee", language="en", importance=0.8)

    assert store.retrieve("I made an object. Would you like to see it?") == []
    assert store.retrieve("Do I like coffee?")[0].content == "I like coffee"
    store.close()


def test_wake_name_only_never_retrieves_an_unrelated_memory(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory.sqlite3")
    store.remember("こんにちはスタックちゃん左を向いて私がコーヒーが好きだ", language="ja")

    for query in ("Stack-chan.", "スタックちゃん。", "すたっくちゃん！"):
        assert store.retrieve(query) == []
    store.close()


def test_legacy_multi_intent_memory_never_contaminates_questions(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory.sqlite3")
    store.remember(
        "こんにちはスタックちゃん左を向いて私がコーヒーが好きだ",
        language="ja",
        kind="explicit",
    )

    assert store.retrieve("スタックちゃんは言葉を読めますか？") == []
    assert store.retrieve("スタックちゃん、サービスを作れますか？") == []
    assert store.retrieve("コーヒーについて教えて") == []
    store.close()


def test_legacy_corrupt_robot_subject_profile_is_never_retrieved(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory.sqlite3")
    store.remember(
        "ユーザーはあなたが苦手です。",
        language="ja",
        kind="profile",
        importance=0.8,
        memory_key="preference:あなた",
    )

    assert store.retrieve("私について何を覚えていますか？") == []
    assert store.retrieve("あなたについて") == []
    store.close()


def test_preferred_name_recall_crosses_the_conversation_language(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory.sqlite3")
    stored = store.capture_automatic_memories(
        "パラスと呼んで。", "わかりました。", "ja"
    )[0]

    assert store.retrieve("Do you know my name?") == [stored]
    assert store.retrieve("私の名前を覚えていますか？") == [stored]
    store.close()


def test_common_conversation_words_do_not_retrieve_unrelated_episodes(
    tmp_path: Path,
) -> None:
    store = MemoryStore(tmp_path / "memory.sqlite3")
    store.record_episode(
        "Can you hear me?",
        "Yes. Your favorite drink is coffee.",
        "en",
    )

    assert store.retrieve("Can you actually move your head left?") == []
    assert store.retrieve("I need a short joke instead.") == []
    assert store.retrieve("coffee") == []
    assert "coffee" in store.retrieve("What did we talk about last time?")[0].content
    store.close()


def test_explicit_memory_extraction_is_bilingual() -> None:
    assert (
        extract_explicit_memory("Please remember that I like coffee.", "en")
        == "I like coffee"
    )
    assert (
        extract_explicit_memory(
            "こんにちは、左を向いて、私が、コーヒーが好きだと覚えてください。", "ja"
        )
        == "私が、コーヒーが好きだ"
    )
    assert (
        extract_explicit_memory(
            "こんにちはスタックちゃん左を向いて私がコーヒーが好きだと覚えてください",
            "ja",
        )
        == "私がコーヒーが好きだ"
    )
    assert (
        extract_explicit_memory(
            "スタックちゃん、メモリーテストの色は紫だと覚えてください。", "ja"
        )
        == "メモリーテストの色は紫だ"
    )
    assert extract_explicit_memory("Don't remember that I like coffee.", "en") is None
    assert extract_explicit_memory("コーヒーが好きだと覚えないで。", "ja") is None


def test_remember_once_deduplicates_exact_memory(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory.sqlite3")
    first, first_created = store.remember_once("I like coffee", language="en")
    second, second_created = store.remember_once("I like coffee", language="en")

    assert first_created is True
    assert second_created is False
    assert first.id == second.id
    store.close()


def test_natural_japanese_question_retrieves_shared_subject(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory.sqlite3")
    store.remember("私の好きな色は紫です", language="ja", importance=0.9)
    store.remember("スタックちゃんは東京にいます", language="ja", importance=0.7)

    results = store.retrieve("私の好きな色は何ですか？")

    assert results
    assert results[0].content == "私の好きな色は紫です"
    assert store.retrieve("全く関係のない質問") == []
    store.close()


def test_memory_survives_store_reopen_in_both_languages(tmp_path: Path) -> None:
    path = tmp_path / "memory.sqlite3"
    first = MemoryStore(path)
    first.remember("my favorite color is lavender", language="en", importance=0.8)
    first.remember("私の好きな飲み物はコーヒーです", language="ja", importance=0.8)
    first.close()

    reopened = MemoryStore(path)
    assert reopened.retrieve("What is my favorite color?")[0].content.endswith(
        "lavender"
    )
    assert reopened.retrieve("私の好きな飲み物は何？")[0].content.endswith(
        "コーヒーです"
    )
    reopened.close()


def test_memory_can_be_listed_and_explicitly_forgotten(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory.sqlite3")
    older = store.remember("older fact", language="en")
    newer = store.remember("newer fact", language="en")

    assert [item.id for item in store.list_recent()] == [newer.id, older.id]
    assert store.forget(older.id) is True
    assert store.forget(older.id) is False
    assert [item.id for item in store.list_recent()] == [newer.id]
    store.close()


def test_profile_extraction_is_bilingual_stable_and_conservative() -> None:
    english = extract_profile_memories("My favorite color is lavender.", "en")
    japanese = extract_profile_memories("私はコーヒーが好きです。", "ja")

    assert [(item.key, item.content) for item in english] == [
        ("favorite:color", "The user's favorite color is lavender.")
    ]
    assert [(item.key, item.content) for item in japanese] == [
        ("preference:コーヒー", "ユーザーはコーヒーが好きです。")
    ]
    assert extract_profile_memories("I like it.", "en") == []
    assert extract_profile_memories("I like tea for now.", "en") == []
    assert extract_profile_memories("What do I like?", "en") == []
    assert extract_profile_memories("私の好きな飲み物は何ですか。", "ja") == []
    assert extract_profile_memories("私の好きな飲み物は何ですか？", "ja") == []
    assert extract_profile_memories("あなたが苦手です。", "ja") == []
    assert extract_profile_memories("スタックちゃんが好きです。", "ja") == []
    assert extract_profile_memories("Call me Paras.", "en")[0].content.endswith(
        "called Paras."
    )
    assert extract_profile_memories("パラスと呼んで。", "ja")[0].content == (
        "ユーザーはパラスと呼ばれたいです。"
    )


def test_automatic_profile_updates_one_semantic_slot(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory.sqlite3")

    first = store.capture_automatic_memories(
        "My favorite color is lavender.", "That is a calm color.", "en"
    )[0]
    second = store.capture_automatic_memories(
        "My favorite color is teal.", "Teal has a lively balance.", "en"
    )[0]

    assert first.id == second.id
    assert second.content == "The user's favorite color is teal."
    profile = store.retrieve("What do you know about me?")
    assert [item.content for item in profile] == [
        "The user's favorite color is teal."
    ]
    assert profile[0].kind == "profile"

    liked = store.capture_automatic_memories(
        "I like coffee.", "Coffee can be wonderfully aromatic.", "en"
    )[0]
    disliked = store.capture_automatic_memories(
        "I don't like coffee.", "I will keep that preference in mind.", "en"
    )[0]
    assert liked.id == disliked.id
    assert disliked.content == "The user dislikes coffee."
    store.close()


def test_subjectless_japanese_preference_correction_updates_the_same_slot(
    tmp_path: Path,
) -> None:
    store = MemoryStore(tmp_path / "memory.sqlite3")

    liked = store.capture_automatic_memories(
        "私はコーヒーが好きです。", "覚えておきます。", "ja"
    )[0]
    disliked = store.capture_automatic_memories(
        "コーヒーは好きじゃないよ。", "訂正して覚えておきます。", "ja"
    )[0]

    assert liked.id == disliked.id
    assert disliked.content == "ユーザーはコーヒーが苦手です。"
    assert store.retrieve("私のコーヒーの好みは？") == [disliked]
    store.close()


def test_episodes_are_hidden_bounded_retrievable_and_expire(tmp_path: Path) -> None:
    store = MemoryStore(
        tmp_path / "memory.sqlite3", episode_limit=2, episode_retention_days=1
    )
    for topic in ("gardening", "astronomy", "Japanese trains"):
        store.capture_automatic_memories(
            f"Tell me something thoughtful about {topic}.",
            f"Here is a substantive answer about {topic} and why it is interesting.",
            "en",
        )

    assert store.list_recent() == []
    episodes = store.list_recent(include_episodes=True)
    assert len(episodes) == 2
    assert all(item.kind == "episode" for item in episodes)
    assert "Japanese trains" in episodes[0].content
    recalled = store.retrieve("What did we talk about last time?")
    assert recalled[0].id == episodes[0].id
    japanese = store.record_episode(
        "雨の日の話をしました。", "雨音は落ち着くリズムを作ります。", "ja"
    )
    assert store.retrieve("前回は何を話しましたか？")[0].id == japanese.id
    assert store.retrieve("What did we talk about last time?")[0].language == "en"
    assert episodes[-1].expires_at is not None
    store.prune_expired(now=episodes[-1].expires_at + 1)
    assert store.list_recent(include_episodes=True) == []
    store.close()


def test_automatic_capture_rejects_sensitive_and_command_turns(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory.sqlite3")

    assert (
        store.capture_automatic_memories(
            "I like insulin for my diabetes.",
            "Let us discuss that carefully.",
            "en",
        )
        == []
    )
    assert (
        store.capture_automatic_memories(
            "Please move your head left.", "I moved my head left.", "en"
        )
        == []
    )
    store.capture_automatic_memories(
        "Explain why rain sounds calming.",
        "A steady rain masks sharp noises and creates a predictable rhythm.",
        "en",
    )
    episode_count = len(store.list_recent(include_episodes=True))
    assert (
        store.capture_automatic_memories(
            "What did we talk about last time?",
            "We discussed why rain sounds calming.",
            "en",
        )
        == []
    )
    assert len(store.list_recent(include_episodes=True)) == episode_count
    store.close()


@pytest.mark.parametrize(
    ("transcript", "response", "language"),
    [
        ("Thank you.", "You're welcome!", "en"),
        ("(door closes)", "It sounds like a door closed.", "en"),
        ("そうですね。", "はい、そうですね。", "ja"),
        ("ありがとう。", "どういたしまして。コーヒーが好きなんだよね。", "ja"),
    ],
)
def test_automatic_episode_capture_rejects_filler_noise_and_memory_echo(
    tmp_path: Path, transcript: str, response: str, language: str
) -> None:
    store = MemoryStore(tmp_path / "memory.sqlite3")

    assert store.capture_episode_memory(transcript, response, language) == []
    assert store.list_recent(include_episodes=True) == []
    store.close()


def test_explicit_request_promotes_matching_profile_to_permanent_memory(
    tmp_path: Path,
) -> None:
    store = MemoryStore(tmp_path / "memory.sqlite3")
    profile = store.capture_automatic_memories(
        "I like coffee.", "Coffee can be wonderfully aromatic.", "en"
    )[0]

    explicit, created = store.remember_once(
        profile.content, language="en", kind="explicit", importance=0.85
    )

    assert created is False
    assert explicit.id == profile.id
    assert explicit.kind == "explicit"
    assert explicit.memory_key is None
    assert explicit.expires_at is None
    store.close()


def test_existing_memory_database_migrates_without_data_loss(tmp_path: Path) -> None:
    path = tmp_path / "memory.sqlite3"
    connection = sqlite3.connect(path)
    connection.execute(
        """CREATE TABLE memories (
               id INTEGER PRIMARY KEY,
               content TEXT NOT NULL,
               language TEXT NOT NULL DEFAULT 'und',
               kind TEXT NOT NULL DEFAULT 'fact',
               importance REAL NOT NULL DEFAULT 0.5,
               created_at REAL NOT NULL,
               last_accessed_at REAL NOT NULL,
               access_count INTEGER NOT NULL DEFAULT 0
           )"""
    )
    connection.execute(
        """INSERT INTO memories(
               content, language, kind, importance, created_at, last_accessed_at
           ) VALUES ('legacy favorite is green', 'en', 'explicit', 0.8, 10, 10)"""
    )
    connection.commit()
    connection.close()

    store = MemoryStore(path)

    legacy = store.retrieve("legacy")[0]
    assert legacy.content == "legacy favorite is green"
    assert legacy.updated_at == 10
    assert legacy.expires_at is None
    store.close()


@pytest.mark.parametrize(
    ("content", "category"),
    [
        ("My password is the test phrase swordfish", "credential"),
        ("My card number is 4111 1111 1111 1111", "financial"),
        ("I was diagnosed with diabetes", "health"),
        ("I have cancer", "health"),
        ("I am pregnant", "health"),
        ("I take metformin every day", "health"),
        ("私のパスワードはテスト用です", "credential"),
        ("私はがんです", "health"),
        ("私の病気は高血圧です", "health"),
    ],
)
def test_sensitive_memories_are_rejected_before_storage(
    tmp_path: Path, content: str, category: str
) -> None:
    store = MemoryStore(tmp_path / "memory.sqlite3")

    with pytest.raises(SensitiveMemoryError) as raised:
        store.remember_once(content, language="en")

    assert raised.value.category == category
    assert store.list_recent() == []
    store.close()
