"""Write to Spotify for the reverse direction (Apple -> Spotify).

Uses a Spotify web-player bearer token you grab once from open.spotify.com — the
same DevTools pattern as the Apple side, so no app registration. It searches the
catalog (to match Apple tracks) and creates the playlist in your library.

Caveat: a web-player token expires roughly hourly, so grab it right before a
transfer. If it's stale, calls fail cleanly and the CSV remains your fallback.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

from .spotify import Track

API = "https://api.spotify.com/v1"
_ADD_CHUNK = 100  # Spotify accepts up to 100 uris per add call
_PAGE = 100  # max page size for playlist reads


class SpotifyAuthError(Exception):
    """Token missing or expired. Message is safe to show the user."""


class SpotifyApiError(Exception):
    """Spotify refused the request. Message is safe to show."""

    def __init__(self, message: str, code: int | None = None):
        super().__init__(message)
        self.code = code  # HTTP status, when there was one


@dataclass
class WriteResult:
    playlist_id: str
    playlist_url: str
    added: int
    failed: list = field(default_factory=list)


def _headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token.strip()}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def _request(method: str, path: str, token: str, body: dict | None = None, timeout: int = 25):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(API + path, data=data, headers=_headers(token), method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", "replace")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        if e.code in (401, 403):
            raise SpotifyAuthError(
                "Spotify rejected the token (web tokens expire about hourly). Grab a "
                "fresh one from open.spotify.com and save it again."
            ) from e
        raise SpotifyApiError(f"Spotify error {e.code}: {detail}", code=e.code) from e
    except Exception as e:
        raise SpotifyApiError(f"Couldn't reach Spotify ({e}).") from e


def normalize_search(payload: dict) -> list[dict]:
    """Flatten a Spotify /search response into the matcher's candidate shape."""
    items = ((payload or {}).get("tracks") or {}).get("items") or []
    out = []
    for t in items:
        artists = ", ".join(a.get("name", "") for a in (t.get("artists") or []))
        out.append(
            {
                "trackId": t.get("uri"),  # spotify:track:... — used to add to the playlist
                "trackName": t.get("name"),
                "artistName": artists,
                "trackTimeMillis": t.get("duration_ms"),
                "trackViewUrl": (t.get("external_urls") or {}).get("spotify"),
            }
        )
    return out


def search(term: str, token: str, *, limit: int = 8) -> list[dict]:
    q = urllib.parse.urlencode({"q": term, "type": "track", "limit": limit})
    return normalize_search(_request("GET", f"/search?{q}", token))


def searcher(token: str):
    """Return a ``(term) -> [candidate]`` callable for the matcher."""
    def s(term):
        return search(term, token)

    return s


def read_playlist_full(playlist_id: str, token: str):
    """Read a whole playlist via the official API — no 100-track embed cap.

    Upgrades an embed read that hit the cap, when a token is saved. Returns
    ``(name, [Track, ...], truncated=False)`` — the same shape as the no-login
    readers, so the caller can swap the result in transparently."""
    pid = urllib.parse.quote(playlist_id)
    meta = _request("GET", f"/playlists/{pid}?fields=name", token)
    name = meta.get("name") or "Spotify Playlist"

    tracks: list[Track] = []
    offset = 0
    while True:
        q = urllib.parse.urlencode(
            {
                "limit": _PAGE,
                "offset": offset,
                "fields": "total,items(track(name,uri,duration_ms,explicit,artists(name)))",
            }
        )
        page = _request("GET", f"/playlists/{pid}/tracks?{q}", token)
        items = page.get("items") or []
        for it in items:
            t = it.get("track") or {}
            title = (t.get("name") or "").strip()
            if not title:  # local files / removed tracks come back empty
                continue
            tracks.append(
                Track(
                    title=title,
                    artist=", ".join(
                        a.get("name", "") for a in (t.get("artists") or []) if a.get("name")
                    ),
                    duration_ms=t.get("duration_ms"),
                    explicit=bool(t.get("explicit")),
                    spotify_uri=t.get("uri"),
                )
            )
        offset += len(items)
        total = page.get("total")
        if not items or (isinstance(total, int) and offset >= total):
            break

    if not tracks:
        raise SpotifyApiError("The playlist appears to be empty.")
    return name, tracks, False


def _chunks(seq, n):
    for i in range(0, len(seq), n):
        yield seq[i : i + n]


def create_playlist(name: str, description: str, uris: list[str], token: str) -> WriteResult:
    """Create a playlist in your library and add the matched track uris."""
    if not token:
        raise SpotifyAuthError("No Spotify token set. Open Settings to add one.")

    me = _request("GET", "/me", token)
    uid = me.get("id")
    if not uid:
        raise SpotifyApiError("Couldn't read your Spotify account from the token.")

    created = _request(
        "POST",
        f"/users/{urllib.parse.quote(uid)}/playlists",
        token,
        {"name": name, "description": description, "public": False},
    )
    pid = created.get("id")
    if not pid:
        raise SpotifyApiError("Spotify didn't return a playlist id.")

    added, failed = _add_tracks(pid, uris, token)
    url = (created.get("external_urls") or {}).get("spotify") or f"https://open.spotify.com/playlist/{pid}"
    return WriteResult(pid, url, added, failed)


def _add_tracks(playlist_id: str, uris: list[str], token: str) -> tuple[int, list]:
    added, failed = 0, []
    for chunk in _chunks(uris, _ADD_CHUNK):
        try:
            _request("POST", f"/playlists/{playlist_id}/tracks", token, {"uris": chunk})
            added += len(chunk)
        except SpotifyApiError:
            failed.extend(chunk)
    return added, failed


def playlist_exists(playlist_id: str, token: str) -> bool:
    """True if the playlist is still there (it may have been deleted)."""
    try:
        _request("GET", f"/playlists/{urllib.parse.quote(playlist_id)}?fields=id", token)
        return True
    except SpotifyApiError as e:
        if e.code == 404:
            return False
        raise  # a real error shouldn't silently trigger create-a-duplicate


def playlist_track_uris(playlist_id: str, token: str) -> set[str]:
    """All track uris already in a playlist (for the re-sync diff)."""
    pid = urllib.parse.quote(playlist_id)
    uris: set[str] = set()
    offset = 0
    while True:
        q = urllib.parse.urlencode(
            {"limit": _PAGE, "offset": offset, "fields": "total,items(track(uri))"}
        )
        page = _request("GET", f"/playlists/{pid}/tracks?{q}", token)
        items = page.get("items") or []
        for it in items:
            u = (it.get("track") or {}).get("uri")
            if u:
                uris.add(u)
        offset += len(items)
        total = page.get("total")
        if not items or (isinstance(total, int) and offset >= total):
            return uris


def add_to_playlist(playlist_id: str, uris: list[str], token: str) -> WriteResult:
    """Add tracks to an existing playlist (the re-sync path)."""
    added, failed = _add_tracks(playlist_id, uris, token)
    return WriteResult(
        playlist_id, f"https://open.spotify.com/playlist/{playlist_id}", added, failed
    )
