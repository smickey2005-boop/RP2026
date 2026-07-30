# Handoff — UI update for `RP_timing_pi2.py`

Frontend-only refresh of the timing dashboard. **No GPIO, sensor, timing, or race-logic code was
touched.** Everything below is HTML/CSS inside `generate_html()`, plus one new HTTP route to serve
font files.

---

## 1. What to copy to the Raspberry Pi

Only two things:

```
RP_timing_pi2.py        # updated script (replaces the old one)
assets/                 # NEW folder — must sit next to the script
  ├── bison.ttf         # 28 KB  — display font
  └── overpass.woff2    # 39 KB  — body font
```

Final layout on the Pi:

```
/home/pi/whatever/
├── RP_timing_pi2.py
└── assets/
    ├── bison.ttf
    └── overpass.woff2
```

**The `assets/` folder is required.** The script locates it relative to its own file path
(`os.path.dirname(os.path.abspath(__file__)) + /assets`), so the folder must always travel with the
script. If it's missing, the page still loads and works — the fonts just fall back to system
defaults and it looks wrong.

Run exactly as before:

```bash
python3 RP_timing_pi2.py
# browser → http://<pi-ip>:8080
```

No new Python packages. Standard library only, same as before.

### Files NOT needed on the Pi

These are for design work only — do not deploy:

| File | What it is |
|---|---|
| `index.html` | Static snapshot of the generated page, for previewing the design in a browser |
| `preview_server.py` | Fake server that serves `index.html` + dummy race data, so the UI can be viewed without a Pi |
| `logo.png`, `logo-small.png` | Source logo art (already embedded in the script as base64) |
| `nuvex-wordmark.svg` | Source NUVEX wordmark (already embedded in the script as base64) |
| `design-system/` | Brand colour + typography spec and original font files |
| `HANDOFF.md` | This document |

---

## 2. Changes made to `RP_timing_pi2.py`

Four regions changed. Line numbers refer to the updated file.

### 2.1 Logos — lines 365-366

Both logos are still embedded as base64 data URIs inside the script (no external image files, no
internet needed).

| Constant | Before | After |
|---|---|---|
| `_RP_LOGO` | 283 KB base64 — declared PNG but was actually JPEG data | 24 KB base64 — real PNG, new Race Pakistan mark, resized to 394×240 |
| `_NUVEX_LOGO` | 34 KB base64 PNG, **never used** (header showed plain text) | 20 KB base64 SVG wordmark, now actually displayed |

The old RP logo was a 4379×2665 image being rendered at 40 px tall. Resizing it cut the page weight
massively — see §4.

### 2.2 Page styling — lines 390-445 (the `<style>` block)

Full replacement of the CSS. **The HTML structure is unchanged** — same elements, same IDs, same
order, same two-column grids. Only colours, fonts, and surface treatment changed.

- Fonts switched from Google Fonts (Orbitron + Rajdhani, loaded over the internet) to **local files**
  — Bison for headings/numbers, Overpass for labels/body. The page no longer needs an internet
  connection to render correctly.
- Colour scheme moved from black/red/gold to the official Race Pakistan palette: deep plum canvas,
  Race Orange `#FF5635` accents, Amber `#FFBB00` timing values.
- Start lights got proper bulb rendering (radial gradient + inset shadow when off, glow when on).
  Red lights are ember→crimson so they read clearly as red; the "go" light is `#22C55E`, chosen to
  stay readable from a distance.
- Download button is now an orange pill.
- Header title is centred and larger.

Behaviour is identical — the same CSS classes get toggled by the same JavaScript, so light states,
status messages, and value updates work exactly as before.

### 2.3 New font route — `do_GET` + `_serve_asset()`, around line 575

```python
elif self.path.startswith("/assets/"):
    self._serve_asset()
```

New handler serving font files from `assets/`:

- Only `.woff2`, `.ttf`, `.otf` are served — anything else returns 404.
- Uses `os.path.basename()` on the request path, so `../` directory-traversal requests can't reach
  outside the `assets/` folder.
- Sends a 1-year cache header, so each browser downloads the fonts once.

`/data` and the main page route are untouched.

### 2.4 Header markup — line 452

```html
<!-- before: two text divs -->
<div class="logo-nuvex"><div class="nuvex-text">NUVEX</div><div class="nuvex-sub">THINK INFINITE</div></div>

<!-- after: the real wordmark -->
<div class="logo-nuvex"><img src="{_NUVEX_LOGO}" alt="NUVEX — Think Infinite"></div>
```

This is the only DOM change in the entire file.

---

## 3. Untouched — for reassurance

Everything from the top of the file through line 364 is byte-for-byte identical to the original:

- GPIO pin assignments and setup
- Obstacle detection, sensor calibration, IR handling
- Race state machine, `track_thread`, timing capture
- `record_attempt`, `attempt_stats`, attempt history
- `_serve_json` (the `/data` payload), `start_web_server`, `main()`

The JSON API shape is unchanged, so nothing on the electronics side needs adjusting.

**One incidental change:** the file's line endings converted from CRLF (Windows) to LF (Unix). This
has no effect on Python or on the Pi — worth knowing only if it shows up as a whole-file diff in a
comparison tool. Diff with `diff --strip-trailing-cr` to see the real changes.

---

## 4. Page weight

The dashboard regenerates on every request and isn't cached, so this matters on a Pi over WiFi.

| | Before | After |
|---|---|---|
| Served HTML | 295 KB | 59 KB |
| Script file | 355 KB | 85 KB |

Fonts add 67 KB, but only on the first page load — they're cached for a year after that.

---

## 5. Previewing the design without a Pi

On any machine with Python 3:

```bash
python3 preview_server.py                      # http://localhost:8080
python3 preview_server.py --state IN_PROGRESS  # start lights on
python3 preview_server.py --state OBSTACLE     # amber warning light
python3 preview_server.py --state IDLE         # green light, no data
```

It serves `index.html` with fake race data matching the real `/data` format.

**Note:** `index.html` is *generated output*. Editing it does nothing on the Pi — the real page lives
in the `generate_html()` f-string in `RP_timing_pi2.py`. After changing the script, regenerate the
preview snapshot from it before viewing.
