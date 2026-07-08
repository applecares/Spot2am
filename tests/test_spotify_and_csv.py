import unittest

from spot2am import csvout
from spot2am.matcher import Match
from spot2am.spotify import SpotifyReadError, Track, _clean_artist, parse_playlist_id


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
