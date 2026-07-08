import unittest

from spot2am import applemusic, matcher
from spot2am.spotify import Track


class CatalogNormalizeTests(unittest.TestCase):
    def test_maps_apple_catalog_shape_to_candidate(self):
        payload = {
            "results": {
                "songs": {
                    "data": [
                        {
                            "id": "1811817777",
                            "type": "songs",
                            "attributes": {
                                "name": "DtMF",
                                "artistName": "Bad Bunny",
                                "durationInMillis": 237000,
                                "url": "https://music.apple.com/us/song/1811817777",
                            },
                        }
                    ]
                }
            }
        }
        cands = applemusic.normalize_catalog(payload)
        self.assertEqual(len(cands), 1)
        c = cands[0]
        self.assertEqual(c["trackId"], "1811817777")
        self.assertEqual(c["trackName"], "DtMF")
        self.assertEqual(c["trackTimeMillis"], 237000)
        # And the matcher scores it like any other candidate.
        t = Track(title="DtMF", artist="Bad Bunny", duration_ms=237000)
        self.assertGreaterEqual(matcher.score(t, c), 0.9)

    def test_empty_payload_is_safe(self):
        self.assertEqual(applemusic.normalize_catalog({}), [])
        self.assertEqual(applemusic.normalize_catalog({"results": {}}), [])


class MatchOrderingTests(unittest.TestCase):
    def test_match_all_preserves_playlist_order(self):
        tracks = [Track(title=f"Song {i}", artist="A") for i in range(6)]

        def fake_search(term):
            # echo the queried title back as a perfect catalog hit
            title = term.rsplit(" ", 1)[0]
            return [{"trackId": title, "trackName": title, "artistName": "A"}]

        rows = matcher.match_all(tracks, [fake_search], polite_delay=0)
        self.assertEqual([t.title for t, _ in rows], [t.title for t in tracks])
        self.assertEqual([m.apple_id for _, m in rows], [t.title for t in tracks])

    def test_authoritative_source_wins_over_fallback(self):
        t = Track(title="DtMF", artist="Bad Bunny")
        garbage = lambda term: [{"trackId": "x", "trackName": "DtMF", "artistName": "KIDZ BOP Kids"}]
        catalog = lambda term: [{"trackId": "real", "trackName": "DtMF", "artistName": "Bad Bunny"}]
        # catalog first → real hit short-circuits before the garbage fallback
        m = matcher.match_track(t, [catalog, garbage])
        self.assertEqual(m.apple_id, "real")
        # only garbage available → correctly refuses to match
        m2 = matcher.match_track(t, [garbage])
        self.assertFalse(m2.matched)


if __name__ == "__main__":
    unittest.main()
