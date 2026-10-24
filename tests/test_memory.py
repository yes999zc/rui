"""Memory: curated, not automatic - the old build's rule (虾哥小智 lineage)."""
from __future__ import annotations

from pathlib import Path

from rui.session.memory import MemoryStore


def make(tmp_path: Path) -> MemoryStore:
    return MemoryStore(tmp_path / "memory.json")


def test_only_confirmed_entries_are_injected(tmp_path: Path) -> None:
    store = make(tmp_path)
    store.add("我喜欢周杰伦", confirmed=True)
    cand = store.extract("记住我明天要开会")
    assert cand is not None and cand.confirmed is False
    assert store.confirmed_texts() == ["我喜欢周杰伦"]
    assert [i.text for i in store.items()] == ["我喜欢周杰伦", cand.text]


def test_confirm_turns_a_candidate_into_memory(tmp_path: Path) -> None:
    store = make(tmp_path)
    cand = store.extract("以后都用摄氏度")
    assert cand is not None
    assert store.confirm(cand.id) is not None
    assert store.confirmed_texts() == ["以后都用摄氏度"]


def test_extraction_ignores_ordinary_turns(tmp_path: Path) -> None:
    store = make(tmp_path)
    assert store.extract("今天天气怎么样") is None
    assert store.extract("现在几点") is None
    assert store.items() == []


def test_forget_by_text_and_no_duplicates(tmp_path: Path) -> None:
    store = make(tmp_path)
    store.add("我不吃香菜", confirmed=True)
    assert store.forget_text("香菜") == 1
    assert store.confirmed_texts() == []
    first = store.add("我喜欢安静的歌", confirmed=True)
    again = store.add("我喜欢安静的歌", confirmed=True)
    # audit round 1 #7: add() deduplicates now. 「记住…」 used to write a candidate and
    # then a confirmed row for one sentence; the store is the place that stops it.
    assert again.id == first.id
    assert len(store.confirmed_texts()) == 1


def test_the_store_survives_a_restart(tmp_path: Path) -> None:
    store = make(tmp_path)
    store.add("记住：咖啡不要糖", confirmed=True)
    again = MemoryStore(tmp_path / "memory.json")
    assert again.confirmed_texts() == ["记住：咖啡不要糖"]
