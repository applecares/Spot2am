import json
import unittest

from spot2am import csvout
from spot2am.matcher import Match
from spot2am.spotify import (
    SpotifyReadError,
    Track,
    _clean_artist,
    parse_embed,
    parse_link,
    parse_playlist_id,
)


def _embed_html(entity: dict) -> str:
    blob = json.dumps({"props": {"pageProps": {"state": {"data": {"entity": entity}}}}})
    return f'<script id="__NEXT_DATA__" type="application/json">{blob}</script>'


class ParseIdTests(unittest.TestCase):
    def test_full_url_with_query(self):
        self.assertEqual(
            parse_playlist_id("https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M?si=x"),
            "37i9dQZF1DXcBWIGoYBM5M",
        )

    def test_uri_form(self):
        self.assertEqual(
            parse_playlist_id("spotify:playlist:37i9dQZF1DXcBWIGoYBM5M"),
            "37i9dQZF1DXcBWIGoYBM5M",
        )

    def test_bare_id(self):
        self.assertEqual(
            parse_playlist_id("37i9dQZF1DXcBWIGoYBM5M"), "37i9dQZF1DXcBWIGoYBM5M"
        )

    def test_garbage_raises(self):
        with self.assertRaises(SpotifyReadError):
            parse_playlist_id("https://example.com/not-a-playlist")

    def test_empty_raises(self):
        with self.assertRaises(SpotifyReadError):
            parse_playlist_id("   ")

    def test_nbsp_artists_normalized(self):
        self.assertEqual(_clean_artist("Shakira,\xa0Burna Boy"), "Shakira, Burna Boy")


class ParseLinkTests(unittest.TestCase):
    def test_album_and_track_links(self):
        self.assertEqual(
            parse_link("https://open.spotify.com/album/4aawyAB9vmqN3uQ7FjRGTy?si=x"),
            ("album", "4aawyAB9vmqN3uQ7FjRGTy"),
        )
        self.assertEqual(
            parse_link("spotify:track:6habFhsOp2NvshLv26DqMb"),
            ("track", "6habFhsOp2NvshLv26DqMb"),
        )

    def test_bare_id_is_playlist(self):
        self.assertEqual(
            parse_link("37i9dQZF1DXcBWIGoYBM5M"), ("playlist", "37i9dQZF1DXcBWIGoYBM5M")
        )

    def test_playlist_id_rejects_album(self):
        with self.assertRaises(SpotifyReadError):
            parse_playlist_id("https://open.spotify.com/album/4aawyAB9vmqN3uQ7FjRGTy")


class ParseEmbedTests(unittest.TestCase):
    def test_album_uses_tracklist_shape(self):
        html = _embed_html({
            "type": "album", "name": "Global Warming",
            "trackList": [
                {"title": "Global Warming (feat. Sensato)", "subtitle": "Pitbull, Sensato",
                 "duration": 85400, "isExplicit": True, "uri": "spotify:track:6Omh"},
            ],
        })
        name, tracks, truncated = parse_embed(html, "album")
        self.assertEqual(name, "Global Warming")
        self.assertFalse(truncated)
        self.assertEqual(tracks[0].artist, "Pitbull, Sensato")
        self.assertTrue(tracks[0].explicit)

    def test_track_entity_is_the_track(self):
        html = _embed_html({
            "type": "track", "name": "Despacito", "title": "Despacito",
            "artists": [{"name": "Luis Fonsi"}, {"name": "Daddy Yankee"}],
            "duration": 229360, "isExplicit": False, "uri": "spotify:track:6hab",
        })
        name, tracks, truncated = parse_embed(html, "track")
        self.assertEqual(name, "Despacito")
        self.assertEqual(len(tracks), 1)
        self.assertEqual(tracks[0].artist, "Luis Fonsi, Daddy Yankee")
        self.assertEqual(tracks[0].duration_ms, 229360)
        self.assertEqual(tracks[0].spotify_uri, "spotify:track:6hab")

    def test_empty_album_raises(self):
        with self.assertRaises(SpotifyReadError):
            parse_embed(_embed_html({"type": "album", "name": "X", "trackList": []}), "album")


class CsvTests(unittest.TestCase):
    def _rows(self):
        return [
            (
                Track(title="Blinding Lights", artist="The Weeknd"),
                Match("1488408568", "Blinding Lights", "The Weeknd",
                      "https://music.apple.com/x", 0.98),
            ),
            (
                Track(title="Some Obscure B-Side", artist="Nobody"),
                Match(None, None, None, None, 0.2),
            ),
        ]

    def test_header_and_rows(self):
        out = csvout.to_csv(self._rows())
        lines = out.strip().splitlines()
        self.assertEqual(lines[0], ",".join(csvout.HEADER))
        self.assertIn("matched", out)
        self.assertIn("NOT FOUND", out)
        self.assertIn("1488408568", out)

    def test_title_artist_lead_columns(self):
        # TuneMyMusic import expects Title, Artist first.
        out = csvout.to_csv(self._rows())
        self.assertTrue(out.splitlines()[1].startswith("Blinding Lights,The Weeknd,"))

    def test_safe_filename(self):
        self.assertEqual(csvout.safe_filename("Today's Top Hits! 🎵"), "today-s-top-hits")
        self.assertEqual(csvout.safe_filename(""), "playlist")


if __name__ == "__main__":
    unittest.main()
