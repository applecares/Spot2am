"""Write the conversion result to CSV — the failsafe that runs on every job.

Columns lead with Title, Artist so the file drops straight into TuneMyMusic's
"import from file" if the one-click Apple push isn't available.
"""
from __future__ import annotations

import csv
import io
import re

_SAFE = re.compile(r"[^A-Za-z0-9._-]+")

# Destination columns are direction-neutral ("Match…") since the CSV is used for
# both Spotify -> Apple and Apple -> Spotify. Title, Artist lead for TuneMyMusic.
HEADER = [
    "Title",
    "Artist",
    "Status",
    "Match",
    "Match Artist",
    "Match ID",
    "Confidence",
    "Match URL",
]


def safe_filename(name: str) -> str:
    slug = _SAFE.sub("-", (name or "playlist").strip()).strip("-").lower()
    return (slug or "playlist")[:60]


def to_csv(rows) -> str:
    """``rows`` is a list of ``(track, Match)`` pairs."""
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(HEADER)
    for tr, m in rows:
        w.writerow(
            [
                tr.title,
                tr.artist,
                "matched" if m.matched else "NOT FOUND",
                m.apple_name or "",
                m.apple_artist or "",
                m.apple_id or "",
                f"{m.confidence:.2f}",
                m.apple_url or "",
            ]
        )
    return buf.getvalue()
