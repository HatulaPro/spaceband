# spaceband

A 3D star map of music artists. Artists whose audiences overlap sit close
together and share colours, so genres and scenes show up as coloured
regions of the sky. Search for any artist, fly to it and follow its
most similar artists across the map. There is also a flat 2D view.

## Quick start (Windows)

Double-click `run.bat`, or from a terminal:

```
run.bat                 # first run: ~25,000 artists
run.bat --max 60000     # later: grow the map (it resumes, nothing is re-downloaded)
```

It creates a Python environment, downloads the data, builds the map and
opens http://localhost:8000. Once the map is built, `python serve.py` opens it
again without rebuilding.

Manual steps (any OS, Python 3.10+):

```
pip install -r requirements.txt
python pipeline/crawl.py      # download -> data/raw/        (resumable, Ctrl+C any time)
python pipeline/build.py      # layout   -> web/data/map.json
python serve.py               # explorer -> http://localhost:8000
```

## Android app

Download `spaceband.apk` from the repo's **Releases** page, open it on your phone
and allow "install unknown apps" for your browser or file manager when Android
asks. The map, fonts and 3D engine are bundled, so it works offline. Only the
top tracks and genres in the artist panel are fetched live.

On the phone: drag with one finger to rotate, pinch to zoom, use two fingers to
pan, and tap a star to open it. Close the artist sheet with ×.

The app wraps `web/` with [Capacitor](https://capacitorjs.com). To rebuild it
(Node 20+, JDK 21, Android SDK 36):

```
npm install
python tools/make_icons.py          # launcher icon + splash (only after re-creating android/)
npx cap sync android                # copies web/ (including web/data/map.json) into the app
cd android && ./gradlew assembleRelease
# -> android/app/build/outputs/apk/release/app-release.apk
```

Release builds are signed when `android/app/keystore.properties` and
`android/app/spaceband-release.jks` exist. Both are git-ignored and stored
outside the repo. An update only installs over the existing app if it's signed
with the same key, so keep them safe. Bump `versionCode` in
`android/app/build.gradle` for every new release.

## Where the data comes from

ListenBrainz computes artist similarity from its users' full listening
history: two artists are similar when people play them in the same
listening sessions. It does not publish this as a dump file. The raw listen
dumps are hundreds of GB, so this project mirrors the precomputed result
from the ListenBrainz APIs instead:

| File in `data/raw/` | Source | Contents |
|---|---|---|
| `seeds.json` | `api.listenbrainz.org/1/stats/sitewide/artists` | top artists across several time ranges, where the crawl starts |
| `similar.jsonl` | `labs.api.listenbrainz.org/similar-artists` | up to 100 similar artists with scores for each downloaded artist |
| `popularity.jsonl` | `api.listenbrainz.org/1/popularity/artist` | total plays and listeners, which set the size of each star |
| `mb_tags.jsonl` | `musicbrainz.org/ws/2/artist` | genres of the top artists in each scene, used to name the scenes |

Each artist is one request (six run in parallel, about 12 artists a second).
25,000 artists take about 35 minutes and 165 MB. Batching several artists
into one request is possible, but ListenBrainz then answers for the group as
a whole, so the crawler doesn't do that. The crawl always downloads the
most-referenced artist it hasn't fetched yet, so the well-known core of the map comes first and the long tail after.
The algorithm used is
`session_based_days_7500_session_300_contribution_5_threshold_10_limit_100_filter_True_skip_30`
(about 20 years of sessions). Change `ALGORITHM` in `pipeline/crawl.py` to use
a different one.

## How the map is made (`pipeline/build.py`)

- **Graph:** an artist links to another when either lists the other as
  similar. Scores are normalised per artist.
- **Positions:** UMAP of each artist's neighbourhood vector (cosine), in 3D
  and in 2D. Two artists sit close together when their similar-artist lists
  overlap, not just when they link to each other directly.
- **Cleanup:** MusicBrainz placeholder artists ("[unknown]", "Various
  Artists" and the like) are dropped.
- **Scenes:** Louvain communities on the graph. Each scene is named after
  its most distinctive MusicBrainz genres (`--no-tags` skips this and works
  offline).
- **Colours:** scenes are arranged around the colour wheel so that connected
  scenes get neighbouring hues. Each artist's colour is the mix of its
  neighbours' hues, so artists that bridge two scenes get an in-between
  colour. Brightness follows popularity.

Useful options: `--resolution 3` gives more, smaller scenes (the default is 2). `--neighbors 30`
gives a smoother, more global layout.

## Using the explorer

- `/` focuses search. Arrow keys and Enter pick a result.
- Click a star to open the artist: its scene, genres, listener count, its
  25 most similar artists (lines on the map) and its top tracks, which are
  fetched live from ListenBrainz.
- **Scenes** lists every scene. Pick one to highlight it and fly there.
- **2D / 3D** morphs between the two layouts. **Names** turns labels on and
  off. Esc clears the selection.
- The URL keeps the selected artist (`#<mbid>`), so views can be bookmarked.

## Layout

```
pipeline/crawl.py   download (resumable)
pipeline/build.py   graph -> layout, scenes, colours -> web/data/map.json
pipeline/diag.py    probes the similar-artists API (diag.bat runs it) if it ever changes shape
web/                static explorer (three.js in web/vendor, fonts in web/fonts)
android/            Capacitor Android project (wraps web/)
tools/make_icons.py app icon and splash screen
serve.py            local web server
run.bat             all of the above on Windows
```
