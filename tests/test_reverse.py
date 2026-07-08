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


if __name__ == "__main__":
    unittest.main()
