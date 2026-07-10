import unittest

from spot2am import apple_read, matcher, spotify_write
from spot2am.spotify import Track

APPLE_HTML = """
<html><head>
<meta property="og:title" content="ignored" />
<script type="application/ld+json">{"name":"My AM Playlist","track":[]}</script>
</head><body>
<script id="serialized-server-data" type="application/json">
[{"data":{"sections":[
  {"items":[
    {"id":"track-lockup - pl.x - 123","title":"Song A","artistName":"Artist A","duration":210000,"showExplicitBadge":false},
    {"id":"track-lockup - pl.x - 456","title":"Song B","artistName":"Artist B, Guest","duration":180000}
  ]},
  {"items":[{"id":"other-lockup - pl.x - 9","title":"Not a track","artistName":"x"}]}
]}}]
</script>
</body></html>
"""


class AppleReadTests(unittest.TestCase):
    def test_parses_name_and_tracks(self):
        name, tracks, truncated = apple_read.parse_page(APPLE_HTML)
        self.assertEqual(name, "My AM Playlist")
        self.assertFalse(truncated)
        self.assertEqual(len(tracks), 2)  # the non track-lockup item is ignored
        self.assertEqual((tracks[0].title, tracks[0].artist), ("Song A", "Artist A"))
        self.assertEqual(tracks[0].duration_ms, 210000)
        self.assertEqual(tracks[1].artist, "Artist B, Guest")

    def test_url_validation(self):
        good = "https://music.apple.com/us/playlist/todays-hits/pl.abc123"
        self.assertEqual(apple_read.valid_url(good + "?x=1"), good + "?x=1")
        with self.assertRaises(apple_read.AppleReadError):
            apple_read.valid_url("https://open.spotify.com/playlist/xyz")

    def test_missing_blob_raises(self):
        with self.assertRaises(apple_read.AppleReadError):
            apple_read.parse_page("<html><body>no data</body></html>")


ALBUM_HTML = APPLE_HTML.replace("pl.x", "1440935467")

SONG_HTML = """
<html><head>
<script type="application/ld+json">
{"@context":"http://schema.org","@type":"MusicComposition","name":"Welcome To New York",
 "audio":{"@type":"MusicRecording","name":"Welcome To New York","duration":"PT3M32S",
          "byArtist":[{"@type":"MusicGroup","name":"Taylor Swift"}]}}
</script>
</head><body></body></html>
"""


class AppleLinkTests(unittest.TestCase):
    def test_parse_link_kinds(self):
        pl = "https://music.apple.com/us/playlist/todays-hits/pl.abc123"
        self.assertEqual(apple_read.parse_link(pl), ("playlist", pl, None))
        al = "https://music.apple.com/us/album/global-warming/1440935467"
        self.assertEqual(apple_read.parse_link(al), ("album", al, None))
        sg = "https://music.apple.com/us/song/welcome-to-new-york/1440935802"
        self.assertEqual(apple_read.parse_link(sg), ("song", sg, None))
        # a song shared through its album page (?i=) is kind "song"
        ai = "https://music.apple.com/us/album/welcome/1440935467?i=1440935802"
        self.assertEqual(apple_read.parse_link(ai), ("song", ai, "1440935802"))

    def test_album_missing_numeric_id_raises(self):
        with self.assertRaises(apple_read.AppleReadError):
            apple_read.parse_link("https://music.apple.com/us/album/name-only")

    def test_album_page_parses_like_playlist(self):
        name, tracks, truncated = apple_read.parse_page(ALBUM_HTML)
        self.assertEqual(len(tracks), 2)
        self.assertFalse(truncated)

    def test_song_id_filter_picks_one_row(self):
        _, tracks, _ = apple_read.parse_page(ALBUM_HTML, song_id="456")
        self.assertEqual(len(tracks), 1)
        self.assertEqual(tracks[0].title, "Song B")
        _, none_found, _ = apple_read.parse_page(ALBUM_HTML, song_id="999")
        self.assertEqual(none_found, [])

    def test_song_page_ld_json(self):
        name, tracks, truncated = apple_read.parse_song_page(SONG_HTML)
        self.assertEqual(name, "Welcome To New York")
        self.assertEqual(len(tracks), 1)
        self.assertEqual(tracks[0].artist, "Taylor Swift")
        self.assertEqual(tracks[0].duration_ms, 212000)
        self.assertFalse(truncated)

    def test_iso_duration(self):
        self.assertEqual(apple_read._iso_duration_ms("PT1H2M3S"), 3723000)
        self.assertIsNone(apple_read._iso_duration_ms("garbage"))
        self.assertIsNone(apple_read._iso_duration_ms(""))

    def test_link_key_is_stable_across_link_variants(self):
        self.assertEqual(
            apple_read.link_key("https://music.apple.com/us/playlist/hits/pl.abc123?l=en"),
            "pl.abc123",
        )
        self.assertEqual(
            apple_read.link_key("https://music.apple.com/fr/album/nom-different/1440935467"),
            "1440935467",
        )
        # the ?i= song form keys on the song, not its album
        self.assertEqual(
            apple_read.link_key("https://music.apple.com/us/album/x/1440935467?i=1440935802"),
            "1440935802",
        )


class ResyncTests(unittest.TestCase):
    """The re-sync diff: only songs not already in the destination get added."""

    def test_spotify_playlist_track_uris_paginates(self):
        pages = [
            {"total": 150, "items": [{"track": {"uri": f"spotify:track:{i}"}} for i in range(100)]},
            {"total": 150, "items": [{"track": {"uri": f"spotify:track:{i}"}} for i in range(100, 150)]},
        ]

        def fake(method, path, token, body=None, timeout=25):
            return pages.pop(0)

        orig = spotify_write._request
        spotify_write._request = fake
        try:
            uris = spotify_write.playlist_track_uris("pid", "tok")
        finally:
            spotify_write._request = orig
        self.assertEqual(len(uris), 150)
        self.assertIn("spotify:track:149", uris)

    def test_add_to_playlist_reports_added(self):
        posted = []

        def fake(method, path, token, body=None, timeout=25):
            posted.append(body["uris"])
            return {}

        orig = spotify_write._request
        spotify_write._request = fake
        try:
            r = spotify_write.add_to_playlist("pid", [f"u{i}" for i in range(150)], "tok")
        finally:
            spotify_write._request = orig
        self.assertEqual(r.added, 150)
        self.assertEqual([len(c) for c in posted], [100, 50])  # chunked at the API limit

    def test_apple_catalog_ids_from_library_page(self):
        from spot2am import applemusic
        payload = {"data": [
            {"attributes": {"playParams": {"catalogId": "111"}}},
            {"attributes": {"playParams": {"catalogId": "222"}}},
            {"attributes": {}},  # a row with no playParams is skipped
        ]}
        self.assertEqual(applemusic._catalog_ids(payload), {"111", "222"})


class SpotifyWriteTests(unittest.TestCase):
    def test_normalize_search_shape(self):
        payload = {"tracks": {"items": [
            {"uri": "spotify:track:abc", "name": "Song A",
             "artists": [{"name": "Artist A"}, {"name": "Guest"}],
             "duration_ms": 210000, "external_urls": {"spotify": "https://open.spotify.com/track/abc"}},
        ]}}
        cands = spotify_write.normalize_search(payload)
        self.assertEqual(len(cands), 1)
        c = cands[0]
        self.assertEqual(c["trackId"], "spotify:track:abc")   # uri used to add to playlist
        self.assertEqual(c["trackName"], "Song A")
        self.assertEqual(c["artistName"], "Artist A, Guest")
        self.assertEqual(c["trackTimeMillis"], 210000)

    def test_matcher_scores_spotify_candidate(self):
        t = Track(title="Song A", artist="Artist A", duration_ms=210000)
        cand = spotify_write.normalize_search({"tracks": {"items": [
            {"uri": "spotify:track:abc", "name": "Song A", "artists": [{"name": "Artist A"}], "duration_ms": 210200}]}})[0]
        self.assertGreaterEqual(matcher.score(t, cand), 0.9)

    def test_empty_payload(self):
        self.assertEqual(spotify_write.normalize_search({}), [])


class ReadPlaylistFullTests(unittest.TestCase):
    """Full-playlist read over the official API (the 100-track-cap upgrade)."""

    def _fake_request(self, pages):
        calls = []

        def fake(method, path, token, body=None, timeout=25):
            calls.append(path)
            if "?fields=name" in path:
                return {"name": "Big Playlist"}
            return pages.pop(0)

        return fake, calls

    def test_paginates_past_100(self):
        item = lambda n: {"track": {"name": n, "uri": f"spotify:track:{n}",
                                    "artists": [{"name": "A"}], "duration_ms": 1000}}
        pages = [
            {"total": 150, "items": [item(f"t{i}") for i in range(100)]},
            {"total": 150, "items": [item(f"t{i}") for i in range(100, 150)]},
        ]
        fake, calls = self._fake_request(pages)
        orig = spotify_write._request
        spotify_write._request = fake
        try:
            name, tracks, truncated = spotify_write.read_playlist_full("pid123", "tok")
        finally:
            spotify_write._request = orig
        self.assertEqual(name, "Big Playlist")
        self.assertEqual(len(tracks), 150)
        self.assertFalse(truncated)
        self.assertEqual(tracks[-1].title, "t149")
        self.assertEqual(len(calls), 3)  # name + two pages, no extra page fetch

    def test_skips_local_and_removed_tracks(self):
        pages = [{"total": 2, "items": [
            {"track": {"name": "Real Song", "uri": "spotify:track:x",
                       "artists": [{"name": "A"}, {"name": "B"}]}},
            {"track": None},
        ]}]
        fake, _ = self._fake_request(pages)
        orig = spotify_write._request
        spotify_write._request = fake
        try:
            _, tracks, _ = spotify_write.read_playlist_full("pid", "tok")
        finally:
            spotify_write._request = orig
        self.assertEqual(len(tracks), 1)
        self.assertEqual(tracks[0].artist, "A, B")


if __name__ == "__main__":
    unittest.main()
