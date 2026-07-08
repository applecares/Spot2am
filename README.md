# spot2am

Paste a **public playlist link** and move it between **Spotify** and **Apple Music** —
no login needed to read the source. Built for the "I found a playlist online and want
it in my library" case. Works **both directions**.

```
./run.sh
```

Then open **http://127.0.0.1:8787** (it opens automatically), pick a direction with the
toggle, paste a playlist URL, and hit **Convert**.

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

## The other direction: Apple Music → Spotify

Flip the toggle to **Apple → Spotify** and paste a public
`music.apple.com/…/playlist/…` link. It reads the Apple playlist with no login (from
Apple's public page), then:

- **With a Spotify token** (Settings): it matches each track on Spotify and creates the
  playlist in your library.
- **Without one:** it still reads the playlist and writes the CSV — upload that to
  [TuneMyMusic](https://www.tunemymusic.com/transfer) to land it in Spotify.

The one asymmetry: unlike Apple's months-long token, a **Spotify web-player token
expires ~hourly**, so grab a fresh one right before transferring.

## Optional: one-click push setup

No paid developer account needed for either service — grab the token(s) from the web
player once.

**For Spotify → Apple Music** (Apple tokens last months):

1. Open `music.apple.com`, sign in.
2. DevTools → **Network** tab → click any song.
3. Open a request to `amp-api.music.apple.com` → **Headers**.
4. `authorization: Bearer <…>` → **Developer token**.
5. `media-user-token: <…>` → **Media-user token**.

**For Apple Music → Spotify** (token expires ~hourly):

1. Open `open.spotify.com`, sign in.
2. DevTools → **Network** tab → play a song.
3. Open a request to `api.spotify.com` → **Headers**.
4. `authorization: Bearer <…>` → **Spotify token**.

Paste into **Settings** in the app. Tokens are stored locally in `config.json`
(gitignored, `rw-------`) and never leave your machine except to Apple/Spotify.

## Safety

- Runs only on `127.0.0.1` (your machine) — never exposed to the network.
- A **local-only guard** refuses cross-origin / non-loopback requests, so a website you
  visit can't POST to the app (CSRF / DNS-rebinding).
- Tokens live in `config.json` with `rw-------` (0600) permissions and are gitignored —
  sent to no one but Apple/Spotify.
- Song titles are HTML-escaped in the page; the CSV download is path-sanitized; all
  outbound calls are HTTPS.
- Caveat: the match/push paths use the undocumented web-player tokens (no paid developer
  account). If a service rotates them, those features fail cleanly and fall back to the CSV.

## Notes

- Everything runs locally. No accounts, no server it phones home to, no telemetry.
- Only dependency is Flask; the rest is the Python standard library.
- `python3 -m unittest` runs the offline unit tests (URL parsing, matching, CSV, catalog).
