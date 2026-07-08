# spot2am

Paste a **public Spotify playlist link**, get an **Apple Music** playlist — without
signing into Spotify. Built for the "I found a playlist online and want it in Apple
Music" case.

```
./run.sh
```

Then open **http://127.0.0.1:8787** (it opens automatically), paste a Spotify
playlist URL, and hit **Convert**.

## How it works — and why it can't break on you

Three independent stages, each with a fallback, so you always get *something* usable:

1. **Read the playlist — no Spotify account.** Parses Spotify's public embed page
   (`open.spotify.com/embed/playlist/…`). No login, no API key.
   *Limit:* the embed returns at most **100 tracks**; longer playlists are flagged.
2. **Match to Apple Music.** Two sources, tried in order: the **Apple Music catalog**
   (authoritative — used when your tokens are set; finds stylized or brand-new titles
   the free lookup can't, e.g. Bad Bunny's "DtMF"), falling back to Apple's free
   **iTunes Search API**. Matching is conservative — only a confident title+artist
   match counts, and a matching title with a totally different artist (covers, KIDZ
   BOP, karaoke) is refused unless the duration also lines up. The rest are flagged so
   you can add those few by hand.
3. **A CSV is written every run** (`exports/…csv`) *before* anything else can fail.
   This is your permanent backup and your universal fallback.

### Landing the playlist in Apple Music — two ways

- **One-click push** (optional setup, below): creates the playlist directly in your
  library.
- **CSV fallback** (always works): upload the CSV to
  [TuneMyMusic](https://www.tunemymusic.com/transfer) → choose **File** as the source
  → connect Apple Music. Free up to 500 tracks.
- **If the reader ever breaks** (Spotify tightens access): paste the playlist URL
  straight into TuneMyMusic, which reads it server-side.

## Optional: one-click push setup

No paid Apple Developer account needed. Grab two tokens from the web player once
(they last months):

1. Open `music.apple.com`, sign in.
2. DevTools → **Network** tab → click any song.
3. Open a request to `amp-api.music.apple.com` → **Headers**.
4. `authorization: Bearer <…>` → **Developer token**.
5. `media-user-token: <…>` → **Media-user token**.

Paste both into **Settings** in the app. Tokens are stored locally in
`config.json` (gitignored) and never leave your machine except to Apple.

## Safety

- Runs only on `127.0.0.1` (your machine) — never exposed to the network.
- A **local-only guard** refuses cross-origin / non-loopback requests, so a website you
  visit can't POST to the app (CSRF / DNS-rebinding).
- Apple tokens live in `config.json` with `rw-------` (0600) permissions and are
  gitignored — sent to no one but Apple.
- Song titles are HTML-escaped in the page; the CSV download is path-sanitized; all
  outbound calls are HTTPS.
- Caveat: the catalog-search and push paths use the undocumented web-player tokens (no
  paid Apple Developer account). If Apple rotates them, those two features fail cleanly
  and fall back to the CSV.

## Notes

- Everything runs locally. No accounts, no server it phones home to, no telemetry.
- Only dependency is Flask; the rest is the Python standard library.
- `python3 -m unittest` runs the offline unit tests (URL parsing, matching, CSV, catalog).
