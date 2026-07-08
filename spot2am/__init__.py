"""spot2am — paste a public Spotify playlist link, get an Apple Music playlist.

No Spotify account, no API keys. The reader parses Spotify's public embed page;
matching uses Apple's free iTunes Search API; the optional one-click push uses
web-player tokens you grab once. A CSV is written on every run as the failsafe.
"""

__version__ = "1.0.0"
