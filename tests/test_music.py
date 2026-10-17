from lantern import music as music_mod
from lantern.embedding.base import format_document
from lantern.embedding.fake import FakeEmbedder
from lantern.search.hybrid import search
from lantern.search.music import music_search
from lantern.store import db

SONGS = [
    {"id": "A1", "title": "SKELETONS", "artist": "Travis Scott", "album": "ASTROWORLD", "track": 5, "downloaded": True},
    {"id": "A2", "title": "STARGAZING", "artist": "Travis Scott", "album": "ASTROWORLD", "track": 1},
    {"id": "A3", "title": "MY EYES", "artist": "Travis Scott", "album": "UTOPIA", "track": 4},
    {
        "id": "B1",
        "title": "Swimming Pools (Drank)",
        "artist": "Kendrick Lamar",
        "album": "good kid, m.A.A.d city",
        "track": 17,
    },
    {"id": "C1", "title": "Heartbeat", "artist": "Childish Gambino", "album": "Camp", "track": 6},
    {
        "id": "D1",
        "title": "Fair Trade (feat. Travis Scott)",
        "artist": "Drake",
        "album": "Certified Lover Boy",
        "track": 5,
    },
]
PLAYLISTS = [
    {"id": "P1", "name": "🔥🔥🔥", "songs": ["A1", "A2", "A3", "B1", "C1"]},
    {"id": "P2", "name": "Siłownia", "songs": ["A1", "B1"]},
]


def stored(conn, embedder=None):
    embedder = embedder or FakeEmbedder()
    model_id = db.ensure_model(conn, embedder.model_info())
    embed = lambda docs: embedder.embed_text([format_document(None, d) for d in docs])  # noqa: E731
    return music_mod.store_music(conn, music_mod.items_from(SONGS, PLAYLISTS), embed, model_id)


def test_vectors_only_for_new_or_changed_songs(conn):
    assert stored(conn) == 8
    assert stored(conn) == 0


def test_a_title_finds_the_song_in_the_context_of_its_playlist(conn):
    stored(conn)
    hit = music_search(conn, "skeletons")[0]
    assert (hit.id, hit.match) == ("A1", "title_exact")
    assert hit.playlist_name == "Siłownia"  # the smaller playlist holding it: the closer context
    resp = search(conn, FakeEmbedder(), "skeletons", log=False)
    assert resp.sections()[0][0] == "music" and "music" in resp.categories


def test_artist_and_album_words_with_polish_endings(conn):
    stored(conn)
    found = [h.id for h in music_search(conn, "piosenki travisa scotta", limit=10)]
    assert set(found[:3]) == {"A1", "A2", "A3"} and found[3:] == ["D1"]  # his own songs first, guest spots after
    album = music_search(conn, "astroworld", limit=10)
    assert [h.id for h in album] == ["A2", "A1"]  # the album in its track order
    assert music_search(conn, "swimming pools")[0].id == "B1"  # "(Drank)" does not stop an exact title


def test_playlists_by_name_or_by_the_word(conn):
    stored(conn)
    assert music_search(conn, "silownia")[0].kind == "playlist"
    lists = music_search(conn, "playlista")
    assert [h.id for h in lists] == ["P1", "P2"] and lists[0].count == 5  # biggest first


def test_one_word_that_only_starts_a_title_stays_below_sure_files(conn):
    stored(conn)
    resp = search(conn, FakeEmbedder(), "heart", log=False)
    assert resp.music and resp.section_scores["music"] < 0.72


def test_the_music_filter_and_clearing(conn):
    stored(conn)
    only = search(conn, FakeEmbedder(), "travis", log=False, category="music")
    assert only.category == "music" and len(only.music) == 4 and only.results == [] and only.apps == []
    model_id = db.ensure_model(conn, FakeEmbedder().model_info())
    music_mod.store_music(conn, [], lambda docs: [], model_id)
    assert music_search(conn, "skeletons") == []


def test_each_source_replaces_only_its_own_songs(conn):
    stored(conn)
    embedder = FakeEmbedder()
    model_id = db.ensure_model(conn, embedder.model_info())
    embed = lambda docs: embedder.embed_text([format_document(None, d) for d in docs])  # noqa: E731
    spotify = music_mod.items_from(
        [{"id": "sp-3eekarcy7kvgVJJE", "title": "Skeletons", "artist": "Travis Scott", "album": "ASTROWORLD"}],
        [{"id": "sp-37i9dQZF1DX", "name": "Liked Songs", "songs": ["sp-3eekarcy7kvgVJJE"]}],
        source="spotify",
    )
    assert music_mod.store_music(conn, spotify, embed, model_id, source="spotify") == 2
    hits = music_search(conn, "skeletons", limit=10)
    assert {h.source for h in hits} == {"apple", "spotify"}
    spotify_hit = next(h for h in hits if h.source == "spotify")
    assert spotify_hit.playlist_name == "Liked Songs"  # the context comes from the same source
    music_mod.store_music(conn, [], embed, model_id, source="spotify")  # Spotify switched off
    assert {h.source for h in music_search(conn, "skeletons", limit=10)} == {"apple"}


def test_a_whole_artist_or_album_name_comes_before_files(conn):
    stored(conn)
    assert music_search(conn, "travisa scotta")[0].match == "artist_exact"
    assert music_search(conn, "scott")[0].match == "artist"  # part of the name: weaker
    assert music_search(conn, "astroworld")[0].match == "album_exact"
    resp = search(conn, FakeEmbedder(), "travisa scotta", log=False)
    assert resp.section_scores["music"] > 0.72  # above files that contain the words and agree by meaning


def test_numbers_alone_are_not_songs(conn):
    stored(conn)
    assert music_search(conn, "2+2*20") == [] and music_search(conn, "17") == []
