import unittest

from spot2am.matcher import Match, primary_artist, score
from spot2am.spotify import Track


class FakeCand(dict):
    pass


def cand(name, artist, ms=None):
    d = {"trackName": name, "artistName": artist}
    if ms is not None:
        d["trackTimeMillis"] = ms
    return d


class PrimaryArtistTests(unittest.TestCase):
    def test_drops_features_and_collaborators(self):
        self.assertEqual(primary_artist("Shakira, Burna Boy"), "Shakira")
        self.assertEqual(primary_artist("Drake feat. Rihanna"), "Drake")
        self.assertEqual(primary_artist("Calvin Harris & Dua Lipa"), "Calvin Harris")
        self.assertEqual(primary_artist("Jack Harlow x Lil Nas X"), "Jack Harlow")
        self.assertEqual(primary_artist("Solo Artist"), "Solo Artist")


class ScoreTests(unittest.TestCase):
    def test_exact_match_is_high(self):
        t = Track(title="Blinding Lights", artist="The Weeknd", duration_ms=200000)
        s = score(t, cand("Blinding Lights", "The Weeknd", 200040))
        self.assertGreaterEqual(s, 0.9)

    def test_parenthetical_and_feature_ignored(self):
        t = Track(title="Stay (with Justin Bieber)", artist="The Kid LAROI, Justin Bieber")
        s = score(t, cand("Stay", "The Kid LAROI & Justin Bieber"))
        self.assertGreaterEqual(s, Match(None, None, None, None, 0).confidence + 0.5)
        self.assertTrue(s >= 0.6)

    def test_wrong_song_is_low(self):
        t = Track(title="Blinding Lights", artist="The Weeknd")
        s = score(t, cand("Levitating", "Dua Lipa"))
        self.assertLess(s, 0.5)

    def test_duration_penalty_separates_versions(self):
        t = Track(title="One More Time", artist="Daft Punk", duration_ms=200000)
        radio = score(t, cand("One More Time", "Daft Punk", 200500))
        extended = score(t, cand("One More Time", "Daft Punk", 600000))
        self.assertGreater(radio, extended)


if __name__ == "__main__":
    unittest.main()
