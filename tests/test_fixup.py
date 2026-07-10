import unittest

try:
    import flask  # noqa: F401
    HAS_FLASK = True
except ImportError:  # the rest of the suite must still run without flask
    HAS_FLASK = False

if HAS_FLASK:
    import app as spotapp
    from spot2am.matcher import Match
    from spot2am.spotify import Track


@unittest.skipUnless(HAS_FLASK, "flask is not installed (run.sh installs it)")
class FixupChooseTests(unittest.TestCase):
    def setUp(self):
        self.client = spotapp.app.test_client()
        t1 = Track(title="Song A", artist="Artist A")
        t2 = Track(title="Rare One", artist="Nobody")
        rows = [
            (t1, Match("111", "Song A", "Artist A", None, 0.95)),
            (t2, Match(None, None, None, None, 0.1)),
        ]
        spotapp.JOBS["testjob"] = {
            "name": "P", "tracks": [t1, t2], "direction": "s2a",
            "sync_key": None, "ids": ["111"], "csv": None, "rows": rows,
        }

    def tearDown(self):
        spotapp.JOBS.pop("testjob", None)

    def test_choose_updates_ids_and_counts(self):
        r = self.client.post("/fixup/choose", json={
            "job_id": "testjob", "i": 1, "id": "222",
            "name": "Rare One", "artist": "Nobody",
        })
        d = r.get_json()
        self.assertTrue(d["ok"])
        self.assertEqual(d["matched"], 2)
        self.assertEqual(d["total"], 2)
        self.assertEqual(spotapp.JOBS["testjob"]["ids"], ["111", "222"])

    def test_choose_row_out_of_range(self):
        r = self.client.post("/fixup/choose", json={"job_id": "testjob", "i": 9, "id": "x"})
        self.assertEqual(r.status_code, 400)

    def test_choose_requires_id(self):
        r = self.client.post("/fixup/choose", json={"job_id": "testjob", "i": 1, "id": ""})
        self.assertEqual(r.status_code, 400)

    def test_expired_job(self):
        r = self.client.post("/fixup/choose", json={"job_id": "nope", "i": 0, "id": "1"})
        self.assertEqual(r.status_code, 400)

    def test_non_local_host_blocked(self):
        r = self.client.post(
            "/fixup/choose",
            json={"job_id": "testjob", "i": 1, "id": "2"},
            headers={"Host": "evil.example"},
        )
        self.assertEqual(r.status_code, 403)


if __name__ == "__main__":
    unittest.main()
