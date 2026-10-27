"""「唱首歌」 is a random-play ask, not a title search (0.7.8 field log)."""
from __future__ import annotations

import pytest

from rui.session.rules import is_music_random


@pytest.mark.parametrize("text", [
    "唱首歌",
    "唱首歌吧",
    "唱个歌",
    "唱一首",
    "来一首",
    "来一段",
    "唱一段",
    "放首歌",
    "放音乐",
])
def test_random_play_phrases(text: str) -> None:
    assert is_music_random(text) is True


@pytest.mark.parametrize("text", [
    "放周杰伦的歌",
    "来一首周杰伦的晴天",
    "唱首歌然后讲个笑话",  # over the 8-char cap: not a pure random ask
])
def test_title_like_requests_are_not_random(text: str) -> None:
    assert is_music_random(text) is False
