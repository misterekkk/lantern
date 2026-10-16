"""Instant answers (arithmetic, units, world time), dictionary definitions and shortcuts: all computed on the Mac."""

import sys
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from lantern.answers import instant_answer, world_time
from lantern.embedding.fake import FakeEmbedder
from lantern.search.hybrid import search


@pytest.mark.parametrize(("query", "title"), [
    ("2+2*20", "42"), ("2,5 * 4", "10"), ("15% * 200", "30"), ("sqrt(16)", "4"), ("2^10", "1024"),
    ("5 km to mi", "3.106856 mi"), ("2,5 km na mile", "1,553428 mi"), ("100 f to c", "37.777778 °C"),
    ("10 kg w funtach", "22.046226 lb"), ("3 GB to MB", "3000 MB"), ("90 minut w godzinach", "1.5 h"),
])  # fmt: skip
def test_answers(query, title):
    assert instant_answer(query).title == title


@pytest.mark.parametrize("query", ["2024", "dog on the beach", "def add_two(x, y):", "faktura 245,30", "1/0",
                                   "10 kg w metrach", "9**99999"])  # fmt: skip
def test_no_answer(query):
    assert instant_answer(query) is None


def test_world_time_in_polish_and_english():
    now = datetime(2026, 10, 16, 18, 0, tzinfo=ZoneInfo("Europe/Warsaw"))
    ny = world_time("godzina w nowym jorku", now)
    assert ny.title == "12:00" and ny.subtitle.endswith("−6 h")
    assert world_time("time in tokyo", now).title == "01:00"
    assert world_time("time in atlantis", now) is None


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS dictionaries")
def test_dictionary_only_for_single_words():
    from lantern.dictionary import define

    assert define("dog on the beach") is None and define("2024") is None and define("zzqxv") is None
    entry = define("serendipity") or define("kot")  # whichever dictionary this Mac has active
    assert entry is None or entry.text.lower().startswith(entry.word.lower()[:3])


def test_answer_is_the_top_line_and_shortcuts_follow_apps(conn):
    conn.execute("INSERT INTO shortcuts(id, name) VALUES ('A1', 'Battery Stats')")
    resp = search(conn, FakeEmbedder(), "2+2*20", log=False)
    assert resp.sections()[0][0] == "answer" and resp.answer.title == "42"
    resp = search(conn, FakeEmbedder(), "battery st", log=False)
    assert resp.shortcuts[0].name == "Battery Stats" and "shortcuts" in resp.categories
    assert search(conn, FakeEmbedder(), "2+2", log=False, category="code").answer is None  # a filter: no answer


def test_transcripts_get_names_from_the_file_name_as_a_hint():
    from pathlib import Path

    from lantern.transcription.whisper import vocabulary_hint

    assert vocabulary_hint(Path("/x/team-project-2/Smith_Jones_Brown.mp4")) == (
        "Smith, Jones, Brown, team, project"
    )
    assert vocabulary_hint(Path("/x/DCIM/IMG_2041.MOV")) == ""  # camera names say nothing
