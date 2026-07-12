"""Optional one-click push: create the playlist in your Apple Music library.

This talks to the same private endpoint (``amp-api.music.apple.com``) that the
music.apple.com web player uses, with two tokens you grab once from the browser:

  * a **developer token** — the ``Authorization: Bearer …`` header the web player
    sends (shared, rotates every few months); and
  * your **media-user-token** — the ``media-user-token`` header, which is your
    personal session and the only per-user secret.

No paid Apple Developer account, no MusicKit signing. If a token has expired the
call fails cleanly and the caller falls back to the CSV — you are never stuck.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

API = "https://amp-api.music.apple.com"
_ORIGIN = "https://music.apple.com"
_ADD_CHUNK = 25  # add tracks in small batches so a bad id fails a batch, not all


class AppleAuthError(Exception):
    """Tokens are missing or expired. Message is safe to show the user."""


class AppleApiError(Exception):
    """The Apple endpoint refused the request. Message is safe to show."""

    def __init__(self, message: str, code: int | None = None):
        super().__init__(message)
        self.code = code  # HTTP status, when there was one


@dataclass
class PushResult:
    playlist_id: str
    playlist_url: str
    added: int
    failed_ids: list[str] = field(default_factory=list)


def _headers(bearer: str, user_token: str) -> dict:
    return {
        "Authorization": f"Bearer {bearer.strip()}",
        "media-user-token": user_token.strip(),
        "Origin": _ORIGIN,
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0",
    }


def _request(method: str, path: str, headers: dict, body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(API + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            raw = r.read().decode("utf-8", "replace")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        if e.code in (401, 403):
            raise AppleAuthError(
                "Apple Music rejected your tokens (they expire every so often). "
                "Re-grab both tokens from music.apple.com and save them again."
            ) from e
        raise AppleApiError(f"Apple Music error {e.code}: {detail}", code=e.code) from e
    except Exception as e:
        raise AppleApiError(f"Couldn't reach Apple Music ({e}).") from e


def get_storefront(bearer: str, user_token: str) -> str | None:
    """Return the account's storefront country code (e.g. 'us', 'gb'), or None."""
    try:
        j = _request("GET", "/v1/me/storefront", _headers(bearer, user_token))
        return (j.get("data") or [{}])[0].get("id")
    except Exception:
        return None


def normalize_catalog(payload: dict) -> list[dict]:
    """Flatten an Apple Music catalog /search response into the same candidate
    shape the matcher scores for iTunes results (trackName/artistName/…)."""
    data = (((payload or {}).get("results") or {}).get("songs") or {}).get("data") or []
    out = []
    for song in data:
        attrs = song.get("attributes") or {}
        out.append(
            {
                "trackId": song.get("id"),
                "trackName": attrs.get("name"),
                "artistName": attrs.get("artistName"),
                "trackTimeMillis": attrs.get("durationInMillis"),
                "trackViewUrl": attrs.get("url"),
            }
        )
    return out


def catalog_search(term, storefront, bearer, user_token, *, limit=10) -> list[dict]:
    """Search the real Apple Music streaming catalog (authoritative, unlike the
    stale iTunes Store index). Finds songs iTunes Search can't surface."""
    q = urllib.parse.urlencode({"term": term, "types": "songs", "limit": limit})
    payload = _request(
        "GET", f"/v1/catalog/{storefront}/search?{q}", _headers(bearer, user_token)
    )
    return normalize_catalog(payload)


def catalog_searcher(storefront, bearer, user_token):
    """Return a ``(term) -> [candidate]`` callable for the matcher to plug in."""
    def search(term):
        return catalog_search(term, storefront, bearer, user_token)

    return search


def _chunks(seq, n):
    for i in range(0, len(seq), n):
        yield seq[i : i + n]


def _add_tracks(playlist_id: str, apple_ids: list[str], headers: dict) -> tuple[int, list[str]]:
    added, failed = 0, []
    for chunk in _chunks(apple_ids, _ADD_CHUNK):
        payload = {"data": [{"id": i, "type": "songs"} for i in chunk]}
        try:
            _request(
                "POST",
                f"/v1/me/library/playlists/{playlist_id}/tracks",
                headers,
                payload,
            )
            added += len(chunk)
        except AppleApiError:
            # Retry this batch one id at a time so one bad id doesn't sink 25.
            for one in chunk:
                try:
                    _request(
                        "POST",
                        f"/v1/me/library/playlists/{playlist_id}/tracks",
                        headers,
                        {"data": [{"id": one, "type": "songs"}]},
                    )
                    added += 1
                except AppleApiError:
                    failed.append(one)
    return added, failed


def create_playlist(
    name: str, description: str, apple_ids: list[str], bearer: str, user_token: str
) -> PushResult:
    """Create a library playlist and add the given catalog song ids to it."""
    if not bearer or not user_token:
        raise AppleAuthError("Apple Music tokens aren't set. Open Settings to add them.")

    headers = _headers(bearer, user_token)
    created = _request(
        "POST",
        "/v1/me/library/playlists",
        headers,
        {"attributes": {"name": name, "description": description}},
    )
    try:
        playlist_id = created["data"][0]["id"]
    except (KeyError, IndexError) as e:
        raise AppleApiError("Apple Music didn't return a playlist id.") from e

    added, failed = _add_tracks(playlist_id, apple_ids, headers)
    return PushResult(
        playlist_id=playlist_id,
        playlist_url=f"{_ORIGIN}/library/playlist/{playlist_id}",
        added=added,
        failed_ids=failed,
    )


def playlist_exists(playlist_id: str, bearer: str, user_token: str) -> bool:
    """True if the library playlist is still there (it may have been deleted)."""
    try:
        _request(
            "GET",
            f"/v1/me/library/playlists/{urllib.parse.quote(playlist_id)}",
            _headers(bearer, user_token),
        )
        return True
    except AppleApiError as e:
        if e.code == 404:
            return False
        raise  # a real error shouldn't silently trigger create-a-duplicate


def _catalog_ids(payload: dict) -> set[str]:
    """Catalog song ids out of a library-playlist /tracks page."""
    out = set()
    for item in (payload or {}).get("data") or []:
        cid = ((item.get("attributes") or {}).get("playParams") or {}).get("catalogId")
        if cid:
            out.add(str(cid))
    return out


def playlist_catalog_ids(playlist_id: str, bearer: str, user_token: str) -> set[str]:
    """All catalog song ids already in a library playlist (for the re-sync diff)."""
    headers = _headers(bearer, user_token)
    pid = urllib.parse.quote(playlist_id)
    ids: set[str] = set()
    offset = 0
    while True:
        try:
            j = _request(
                "GET", f"/v1/me/library/playlists/{pid}/tracks?limit=100&offset={offset}", headers
            )
        except AppleApiError as e:
            if e.code == 404 and offset == 0:
                return set()  # Apple 404s the tracks relationship of an empty playlist
            raise
        data = j.get("data") or []
        ids |= _catalog_ids(j)
        offset += len(data)
        if not data or not j.get("next"):
            return ids


def add_to_playlist(
    playlist_id: str, apple_ids: list[str], bearer: str, user_token: str
) -> PushResult:
    """Add songs to an existing library playlist (the re-sync path)."""
    added, failed = _add_tracks(playlist_id, apple_ids, _headers(bearer, user_token))
    return PushResult(
        playlist_id=playlist_id,
        playlist_url=f"{_ORIGIN}/library/playlist/{playlist_id}",
        added=added,
        failed_ids=failed,
    )
