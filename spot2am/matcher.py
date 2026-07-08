"""Match Spotify tracks to Apple Music catalog songs.

Uses Apple's free, public iTunes Search API (no auth, no key). The returned
``trackId`` is the Apple Music catalog id — the same id used both in the CSV and
to add the song to your library in the one-click push.

Scoring is deliberately conservative: a song is only counted as "matched" when
title and artist line up. Everything else is flagged as a miss so you can eyeball
the few that need a manual add, rather than silently getting the wrong song.
"""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

SEARCH_URL = "https://itunes.apple.com/search"
_UA = "spot2am/1.0"
MATCH_THRESHOLD = 0.5

_PAREN = re.compile(r"\s*[\(\[][^\)\]]*[\)\]]")
_FEAT = re.compile(r"\b(feat\.?|featuring|ft\.?|with)\b.*", re.I)
_NONWORD = re.compile(r"[^a-z0-9 ]+")
_WS = re.compile(r"\s+")
_ARTIST_SPLIT = re.compile(r"\s*(?:,|&|\bx\b|\bfeat\.?\b|\bft\.?\b|\bwith\b)\s*", re.I)


@dataclass
class Match:
    apple_id: str | None
    apple_name: str | None
    apple_artist: str | None
    apple_url: str | None
    confidence: float

    @property
    def matched(self) -> bool:
        return self.apple_id is not None and self.confidence >= MATCH_THRESHOLD


# Sentinel for "we couldn't even search" (reverse direction with no Spotify token).
NO_MATCH = Match(None, None, None, None, 0.0)


def _norm(s: str) -> str:
    s = (s or "").lower()
    s = _PAREN.sub(" ", s)
    s = _FEAT.sub(" ", s)
    s = _NONWORD.sub(" ", s)
    return _WS.sub(" ", s).strip()


def primary_artist(artist: str) -> str:
    """The lead artist — drops features, collaborators, and 'with' credits."""
    parts = _ARTIST_SPLIT.split(artist or "", maxsplit=1)
    return parts[0].strip() if parts else (artist or "").strip()


def _sim(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    ta, tb = set(a.split()), set(b.split())
    if a in b or b in a:
        return 0.9
    union = ta | tb
    return len(ta & tb) / len(union) if union else 0.0


def score(track, cand: dict) -> float:
    """Confidence 0..1 that ``cand`` (a search result) is ``track``."""
    title_sim = _sim(_norm(track.title), _norm(cand.get("trackName", "")))
    artist_sim = _sim(_norm(primary_artist(track.artist)), _norm(cand.get("artistName", "")))
    s = 0.6 * title_sim + 0.4 * artist_sim

    dur = cand.get("trackTimeMillis")
    dur_diff = abs(track.duration_ms - dur) / 1000 if (track.duration_ms and dur) else None
    if dur_diff is not None:
        if dur_diff <= 3:
            s += 0.05
        elif dur_diff > 20:
            s -= 0.10

    # A matching title with a completely different artist is the classic false
    # positive here — covers, KIDZ BOP, karaoke, troll uploads. Refuse it unless
    # the duration also lines up (which pins it to the real recording).
    if artist_sim == 0.0 and not (dur_diff is not None and dur_diff <= 3):
        s = min(s, 0.45)

    return max(0.0, min(1.0, s))


class SearchThrottled(Exception):
    """iTunes rate-limited us; the caller should not treat this as 'no match'."""


def _search(term: str, country: str, timeout: int) -> list[dict]:
    q = urllib.parse.urlencode(
        {"term": term, "entity": "song", "limit": 8, "country": country}
    )
    req = urllib.request.Request(f"{SEARCH_URL}?{q}", headers={"User-Agent": _UA})
    # iTunes throttles bursts (HTTP 403/429). Back off and retry so a throttled
    # lookup recovers instead of being silently recorded as a missing song.
    backoffs = (0.0, 0.7, 1.8)
    for i, wait in enumerate(backoffs):
        if wait:
            time.sleep(wait)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8", "replace")).get("results", [])
        except urllib.error.HTTPError as e:
            if e.code in (403, 429) and i < len(backoffs) - 1:
                continue
            if e.code in (403, 429):
                raise SearchThrottled(term) from e
            raise
        except urllib.error.URLError:
            if i < len(backoffs) - 1:
                continue
            raise
    return []


def _best(track, results: list[dict]) -> tuple[dict | None, float]:
    best, best_s = None, -1.0
    for c in results:
        s = score(track, c)
        if s > best_s:
            best, best_s = c, s
    return best, max(0.0, best_s)


HIGH_CONFIDENCE = 0.85  # stop searching once a hit this strong is found


def itunes_searcher(country: str = "us", timeout: int = 15):
    """Return a ``(term) -> [candidate]`` callable over the public iTunes index."""
    def search(term):
        return _search(term, country, timeout)

    return search


def match_track(track, searchers) -> Match:
    """Find the best Apple Music match for one track.

    ``searchers`` is an ordered list of ``(term) -> [candidate]`` callables.
    Each is tried in turn and the best candidate across all of them wins; a
    strong early hit short-circuits the rest. Put the most authoritative source
    (the Apple catalog, when tokens are set) first, iTunes as the fallback.
    """
    term = f"{track.title} {primary_artist(track.artist)}"
    best, best_s = None, -1.0
    for search in searchers:
        try:
            cand, s = _best(track, search(term))
        except Exception:
            continue  # throttled / auth / network — fall through to the next source
        if s > best_s:
            best, best_s = cand, s
        if best_s >= HIGH_CONFIDENCE:
            break

    if best is None or best_s < MATCH_THRESHOLD:
        return Match(None, None, None, None, max(0.0, best_s))
    return Match(
        apple_id=str(best.get("trackId")),
        apple_name=best.get("trackName"),
        apple_artist=best.get("artistName"),
        apple_url=best.get("trackViewUrl"),
        confidence=best_s,
    )


def match_all(tracks, searchers, *, polite_delay: float = 0.12):
    """Match a whole playlist. Returns ``(track, Match)`` pairs in playlist order."""
    out = []
    for t in tracks:
        out.append((t, match_track(t, searchers)))
        if polite_delay:
            time.sleep(polite_delay)
    return out
