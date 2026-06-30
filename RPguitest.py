
"""
╔══════════════════════════════════════════════════════════╗
║       RACE PAKISTAN – F1 TIMING SYSTEM                ║
║       Raspberry Pi 4B – Complete Standalone Version      ║
║       NUVEX × Race Pakistan  © 2025                   ║
╚══════════════════════════════════════════════════════════╝

HOW TO RUN:
  pip install RPi.GPIO
  python3 f1_timing_pi.py
  Open browser → http://192.168.18.195:8080

GPIO WIRING:
══════════════════════════════════════════════
  START LIGHTS (5 LEDs):
    LED 1  →  GPIO 17  → 220Ω → LED → GND
    LED 2  →  GPIO 4   → 220Ω → LED → GND
    LED 3  →  GPIO 5   → 220Ω → LED → GND
    LED 4  →  GPIO 18  → 220Ω → LED → GND
    LED 5  →  GPIO 19  → 220Ω → LED → GND

  MASTER START BUTTON:
    GPIO 15  →  Button  →  GND  (internal pull-up)

  REACTION BUTTONS (one per player):
    Player 1 → GPIO 27  →  Button  →  GND
    Player 2 → GPIO 22  →  Button  →  GND

  TRACK 1 (Player 1) — also used for obstacle detection:
    Start IR  →  GPIO 26   (active LOW = car/obstacle present)
    End IR    →  GPIO 6    (active LOW = car/obstacle present)

  TRACK 2 (Player 2) — also used for obstacle detection:
    Start IR  →  GPIO 20   (active LOW = car/obstacle present)
    End IR    →  GPIO 21   (active LOW = car/obstacle present)

  STATUS LED:
    GPIO 13  →  220Ω  →  LED  →  GND

  NOTE: No separate obstacle sensors needed!
        The same Track IR sensors detect obstacles before the race.
        Once master button is pressed → obstacle detection is OFF.
══════════════════════════════════════════════

RACE SEQUENCE:
  1. IDLE: System checks all 4 track sensors for obstacles
  2. If obstacle detected → web shows warning, master button is ignored
  3. Track is clear + master button pressed → race begins
  4. Lights turn on one by one (5 LEDs, 0.5s each)
  5. Lights hold ON for RANDOM 1–3 seconds (keeps players alert!)
  6. ALL lights turn OFF instantly → reaction clock starts
  7. Each player presses their REACTION BUTTON → reaction time recorded
  8. Car crosses START sensor → race time begins
  9. Car crosses END sensor   → race time stops
  10. Results shown on web page (auto-refreshes every 1 second)
"""

import time
import random
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

try:
    import RPi.GPIO as GPIO
except ImportError:
    print("ERROR: RPi.GPIO not found. Run:  pip install RPi.GPIO")
    raise

# ══════════════════════════════════════════════
#  PIN DEFINITIONS
# ══════════════════════════════════════════════
LED_PINS         = [17, 4, 5, 18, 19]  # 5 start lights (in order)

MASTER_BUTTON    = 15                  # Race start button

REACTION_BTN_P1  = 27                  # Player 1 reaction button
REACTION_BTN_P2  = 22                  # Player 2 reaction button

# Track sensors — ALSO used for obstacle detection before race
TRACK1_START     = 26                  # Player 1 – start IR sensor
TRACK1_END       = 6                   # Player 1 – end IR sensor
TRACK2_START     = 20                  # Player 2 – start IR sensor
TRACK2_END       = 21                  # Player 2 – end IR sensor

STATUS_LED       = 13                  # Status LED

# ══════════════════════════════════════════════
#  GPIO SETUP
# ══════════════════════════════════════════════
GPIO.setmode(GPIO.BCM)
GPIO.setwarnings(False)

for pin in LED_PINS:
    GPIO.setup(pin, GPIO.OUT)
    GPIO.output(pin, GPIO.LOW)

GPIO.setup(STATUS_LED, GPIO.OUT)
GPIO.output(STATUS_LED, GPIO.HIGH)

INPUT_PINS = [
    MASTER_BUTTON,
    REACTION_BTN_P1, REACTION_BTN_P2,
    TRACK1_START, TRACK1_END,
    TRACK2_START, TRACK2_END,
]
for pin in INPUT_PINS:
    GPIO.setup(pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)

# ══════════════════════════════════════════════
#  RACE STATES
# ══════════════════════════════════════════════
STATE_IDLE        = "IDLE"
STATE_OBSTACLE    = "OBSTACLE"
STATE_START       = "START"
STATE_IN_PROGRESS = "IN_PROGRESS"
STATE_COMPLETE    = "COMPLETE"

# ══════════════════════════════════════════════
#  SHARED RACE DATA
# ══════════════════════════════════════════════
data_lock       = threading.Lock()
race_state      = STATE_IDLE
lights_off_time = None    # ms timestamp when lights go OFF

p1 = {"reaction": None, "race": None, "total": None}
p2 = {"reaction": None, "race": None, "total": None}

# ── Attempt history (persists across races in one session) ──
# Each entry: {"attempt": N, "p1_total": ms|None, "p2_total": ms|None,
#              "p1_reaction": ms|None, "p2_reaction": ms|None,
#              "p1_race": ms|None, "p2_race": ms|None}
attempt_history = []
attempt_counter = 0

# Sensor baselines (calibrated at boot = clear track level)
track1_start_clear = GPIO.HIGH
track1_end_clear   = GPIO.HIGH
track2_start_clear = GPIO.HIGH
track2_end_clear   = GPIO.HIGH

# ══════════════════════════════════════════════
#  UTILITY
# ══════════════════════════════════════════════
def ms():
    """High-precision millisecond timestamp."""
    return time.perf_counter_ns() // 1_000_000

def obstacle_present():
    """
    Checks all 4 track sensors for obstacles.
    Uses calibrated baselines — if any sensor differs from clear level,
    something is blocking the track.
    Only call this BEFORE the race starts.
    """
    return (
        GPIO.input(TRACK1_START) != track1_start_clear or
        GPIO.input(TRACK1_END)   != track1_end_clear   or
        GPIO.input(TRACK2_START) != track2_start_clear or
        GPIO.input(TRACK2_END)   != track2_end_clear
    )

def calibrate_sensors():
    """Read sensor baselines at boot (track must be clear)."""
    global track1_start_clear, track1_end_clear
    global track2_start_clear, track2_end_clear
    time.sleep(0.6)
    track1_start_clear = GPIO.input(TRACK1_START)
    track1_end_clear   = GPIO.input(TRACK1_END)
    track2_start_clear = GPIO.input(TRACK2_START)
    track2_end_clear   = GPIO.input(TRACK2_END)
    print("✅  Sensors calibrated (track clear baseline saved)")

def wait_master_button():
    """Block until master button is pressed then released."""
    while GPIO.input(MASTER_BUTTON) == GPIO.HIGH:
        time.sleep(0.01)
    while GPIO.input(MASTER_BUTTON) == GPIO.LOW:
        time.sleep(0.01)

def reset_player(d):
    d["reaction"] = None
    d["race"]     = None
    d["total"]    = None

def record_attempt():
    """Called after each race completes. Appends result to attempt_history."""
    global attempt_counter
    attempt_counter += 1
    with data_lock:
        entry = {
            "attempt":     attempt_counter,
            "p1_reaction": p1["reaction"],
            "p1_race":     p1["race"],
            "p1_total":    p1["total"],
            "p2_reaction": p2["reaction"],
            "p2_race":     p2["race"],
            "p2_total":    p2["total"],
        }
        attempt_history.append(entry)

def attempt_stats(history, player="p1"):
    """Returns best time, avg of best 4, all totals, top-4 totals for a player."""
    totals = [e[f"{player}_total"] for e in history if e[f"{player}_total"] is not None]
    if not totals:
        return None, None, [], []
    sorted_t  = sorted(totals)
    best      = sorted_t[0]
    top4      = sorted_t[:4]
    avg_best4 = round(sum(top4) / len(top4)) if top4 else None
    return best, avg_best4, totals, top4

# ══════════════════════════════════════════════
#  TRACK THREAD  (one per player)
# ══════════════════════════════════════════════
def track_thread(player_id, reaction_btn, start_pin, start_clear,
                 end_pin, end_clear, player_dict):
    """
    One thread per track/player.
    Waits for IN_PROGRESS, then:
      1. Reaction button press  → reaction time (from lights OFF)
      2. Car crosses START IR   → race timer starts
      3. Car crosses END IR     → race timer stops, results saved
    """
    global lights_off_time

    while True:

        # ── Wait for race to begin ─────────────────────
        while True:
            with data_lock:
                s = race_state
            if s == STATE_IN_PROGRESS:
                break
            time.sleep(0.02)

        print(f"[Player {player_id}] Race started – waiting for reaction button")

        reaction_time = None
        race_time     = None
        start_time    = None

        # ── STEP 1: Reaction button ────────────────────
        deadline = ms() + 30_000
        while ms() < deadline:
            if GPIO.input(reaction_btn) == GPIO.LOW:
                press_ms = ms()
                with data_lock:
                    loff = lights_off_time
                if loff is not None:
                    reaction_time = max(0, press_ms - loff)
                print(f"[Player {player_id}] Reaction = {reaction_time} ms")
                while GPIO.input(reaction_btn) == GPIO.LOW:
                    time.sleep(0.005)
                break
            with data_lock:
                s = race_state
            if s != STATE_IN_PROGRESS:
                break
            time.sleep(0.004)

        # ── STEP 2: Car crosses START sensor ──────────
        deadline = ms() + 15_000
        while ms() < deadline:
            if GPIO.input(start_pin) != start_clear:
                start_time = ms()
                print(f"[Player {player_id}] Car passed START sensor")
                break
            with data_lock:
                s = race_state
            if s != STATE_IN_PROGRESS:
                break
            time.sleep(0.002)

        # ── STEP 3: Car crosses END sensor ────────────
        if start_time is not None:
            deadline = ms() + 20_000
            while ms() < deadline:
                if GPIO.input(end_pin) != end_clear:
                    end_time  = ms()
                    race_time = end_time - start_time
                    print(f"[Player {player_id}] Car passed END sensor – race = {race_time} ms")
                    break
                with data_lock:
                    s = race_state
                if s != STATE_IN_PROGRESS:
                    break
                time.sleep(0.002)

        # ── Save results ───────────────────────────────
        with data_lock:
            player_dict["reaction"] = reaction_time
            player_dict["race"]     = race_time
            if reaction_time is not None and race_time is not None:
                player_dict["total"] = reaction_time + race_time
            elif race_time is not None:
                player_dict["total"] = race_time

        # ── Wait for IDLE before next race ────────────
        while True:
            with data_lock:
                s = race_state
            if s == STATE_IDLE:
                break
            time.sleep(0.05)


# ══════════════════════════════════════════════
#  HTML PAGE GENERATOR
# ══════════════════════════════════════════════
def generate_html(status_msg):
    with data_lock:
        rs   = race_state
        r1   = dict(p1)
        r2   = dict(p2)
        hist = list(attempt_history)

    # Obstacle check only relevant before race
    obs = obstacle_present() if rs == STATE_IDLE else False

    # Traffic light logic
    top_light = "green"
    red_on    = False
    if obs or rs == STATE_OBSTACLE:
        top_light = "yellow"
    if rs in (STATE_IN_PROGRESS, STATE_START):
        top_light = ""
        red_on    = True
    red_cls = "on" if red_on else ""

    def stat_row(label, value):
        if value is None:
            v_html = '<span class="val dim">--</span>'
        elif value == 0:
            v_html = '<span class="val dim">EMPTY</span>'
        else:
            v_html = f'<span class="val">{value} <span class="unit">ms</span></span>'
        return f'<div class="stat"><span class="lbl">{label}</span>{v_html}</div>'

    red_lights = "".join(f'<div class="redlight {red_cls}"></div>' for _ in range(5))

    # ── Attempt Summary for both players ──────────────────────────────
    def attempt_summary_html(player_key, player_label):
        best, avg4, all_totals, top4 = attempt_stats(hist, player_key)

        def fmt(v):
            return f"{v} ms" if v is not None else "--"

        # Best / Avg boxes
        best_box = f'<div class="sumbox"><div class="sblbl">BEST TIME</div><div class="sbval">{fmt(best)}</div></div>'
        avg_box  = f'<div class="sumbox"><div class="sblbl">AVG OF BEST 4</div><div class="sbval">{fmt(avg4)}</div></div>'

        # All attempts grid (2 columns)
        all_rows = ""
        for i, t in enumerate(all_totals, 1):
            all_rows += f'<div class="att-cell">Attempt {i}: <b>{fmt(t)}</b></div>'
        if not all_rows:
            all_rows = '<div class="att-cell att-empty">No attempts yet</div>'

        # Top 4 list
        top4_rows = ""
        for i, t in enumerate(top4, 1):
            top4_rows += f'<div class="top-cell">Top {i}: <b>{fmt(t)}</b></div>'
        if not top4_rows:
            top4_rows = '<div class="top-cell att-empty">--</div>'

        return f"""
<div class="summary-card">
  <div class="sum-title">ATTEMPT SUMMARY — {player_label}</div>
  <div class="sum-boxes">{best_box}{avg_box}</div>
  <div class="sum-tables">
    <div class="sum-col">
      <div class="sum-col-title">ALL ATTEMPTS</div>
      <div class="att-grid">{all_rows}</div>
    </div>
    <div class="sum-col">
      <div class="sum-col-title">TOP 4 BEST ATTEMPTS</div>
      <div class="top-list">{top4_rows}</div>
    </div>
  </div>
</div>"""

    sum_p1 = attempt_summary_html("p1", "PLAYER 1")
    sum_p2 = attempt_summary_html("p2", "PLAYER 2")

    # ── CSV data for download button ──────────────────────────────────
    csv_rows_js = "Attempt,P1 Reaction (ms),P1 Race (ms),P1 Total (ms),P2 Reaction (ms),P2 Race (ms),P2 Total (ms)\\n"
    for e in hist:
        def sv(v): return str(v) if v is not None else ""
        csv_rows_js += (f"{e['attempt']},{sv(e['p1_reaction'])},{sv(e['p1_race'])},"
                        f"{sv(e['p1_total'])},{sv(e['p2_reaction'])},{sv(e['p2_race'])},"
                        f"{sv(e['p2_total'])}\\n")

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="1">
<title>Race Pakistan – F1 Timing</title>
<style>
@import url('https://fonts.googleapis.com/css2?family=Orbitron:wght@700;900&family=Rajdhani:wght@500;600;700&display=swap');
*,*::before,*::after{{box-sizing:border-box;margin:0;padding:0}}
:root{{
  --red:#e8002d;--gold:#ffd700;--dark:#080808;
  --card:#0f1014;--border:#1c1e24;--text:#dde1ec;--dim:#444;--orange:#ff6a00;
}}
body{{background:var(--dark);color:var(--text);font-family:'Rajdhani',sans-serif;min-height:100vh;overflow-x:hidden}}
body::after{{content:'';position:fixed;inset:0;background:repeating-linear-gradient(0deg,transparent,transparent 2px,rgba(0,0,0,.07) 2px,rgba(0,0,0,.07) 4px);pointer-events:none;z-index:9999}}

/* HEADER */
header{{display:flex;justify-content:space-between;align-items:center;padding:16px 5vw;border-bottom:3px solid var(--red);background:linear-gradient(180deg,#120508 0%,var(--dark) 100%)}}
.logo-rp{{font-family:'Orbitron',monospace;font-weight:900;font-size:clamp(26px,4vw,48px);color:var(--red);letter-spacing:-1px}}
header h1{{font-family:'Orbitron',monospace;font-size:clamp(11px,1.8vw,20px);letter-spacing:3px;text-align:center;line-height:1.6;color:var(--text)}}
header h1 span{{color:var(--red)}}
.logo-nuvex{{font-family:'Orbitron',monospace;font-weight:700;font-size:clamp(14px,2vw,24px);letter-spacing:2px;color:#fff;opacity:.65}}

/* LIGHTS */
.lights-wrap{{display:flex;flex-direction:column;align-items:center;gap:14px;padding:30px 0 18px}}
.top-light{{width:50px;height:50px;border-radius:50%;background:#1a1a1a;border:2px solid #222;transition:background .3s,box-shadow .3s}}
.top-light.green{{background:#00ff6a;box-shadow:0 0 28px #00ff6a,0 0 60px #00ff6a55}}
.top-light.yellow{{background:var(--gold);box-shadow:0 0 28px var(--gold),0 0 60px #ffd70055}}
.red-row{{display:flex;gap:14px}}
.redlight{{width:42px;height:42px;border-radius:50%;background:#180000;border:2px solid #2a0000;transition:background .3s,box-shadow .3s}}
.redlight.on{{background:var(--red);box-shadow:0 0 24px var(--red),0 0 50px #e8002d55}}

/* STATUS */
.status-bar{{text-align:center;padding:13px 20px;font-family:'Orbitron',monospace;font-size:clamp(11px,1.6vw,16px);letter-spacing:3px;color:var(--gold);border-top:1px solid var(--border);border-bottom:1px solid var(--border);margin-bottom:28px;text-transform:uppercase}}

/* CARDS */
.cards{{display:grid;grid-template-columns:1fr 1fr;gap:3vw;padding:0 5vw 36px;max-width:1200px;margin:0 auto}}
@media(max-width:680px){{.cards{{grid-template-columns:1fr}}header{{flex-direction:column;gap:10px;text-align:center}}.summary-section{{flex-direction:column}}}}
.card{{background:var(--card);border:1px solid var(--border);border-top:5px solid var(--red);border-radius:18px;padding:32px 36px 28px;position:relative;overflow:hidden}}
.card::before{{content:'';position:absolute;top:0;left:0;right:0;height:1px;background:linear-gradient(90deg,transparent,#e8002d88,transparent)}}
.ghost{{font-family:'Orbitron',monospace;font-weight:900;font-size:clamp(60px,10vw,110px);color:#ffffff05;position:absolute;right:16px;top:6px;line-height:1;user-select:none;pointer-events:none}}
.card h2{{font-family:'Orbitron',monospace;font-size:clamp(14px,1.8vw,20px);letter-spacing:4px;color:var(--red);margin-bottom:22px;text-transform:uppercase}}
.stat{{display:flex;justify-content:space-between;align-items:center;padding:13px 0;border-bottom:1px solid var(--border)}}
.stat:last-child{{border-bottom:none}}
.lbl{{font-size:clamp(11px,1.3vw,14px);letter-spacing:2px;color:var(--dim);text-transform:uppercase}}
.val{{font-family:'Orbitron',monospace;font-size:clamp(18px,2.4vw,28px);font-weight:700;color:var(--gold)}}
.val.dim{{color:#333;font-size:clamp(14px,1.8vw,20px)}}
.unit{{font-size:.6em;color:var(--dim);font-weight:400}}

/* ── ATTEMPT SUMMARY ── */
.summary-section{{display:flex;gap:3vw;padding:0 5vw 20px;max-width:1200px;margin:0 auto;flex-wrap:wrap}}
.summary-card{{flex:1;min-width:280px;background:#0a0a0e;border:1px solid #2a1a00;border-top:4px solid var(--orange);border-radius:14px;padding:24px 28px 20px;position:relative}}
.sum-title{{font-family:'Orbitron',monospace;font-size:clamp(11px,1.4vw,15px);letter-spacing:3px;color:var(--orange);margin-bottom:18px;text-transform:uppercase}}
.sum-boxes{{display:flex;gap:14px;margin-bottom:20px}}
.sumbox{{flex:1;background:#12100a;border:1px solid #2a1a00;border-radius:8px;padding:12px 14px;text-align:center}}
.sblbl{{font-size:10px;letter-spacing:2px;color:#666;text-transform:uppercase;margin-bottom:6px}}
.sbval{{font-family:'Orbitron',monospace;font-size:clamp(16px,2vw,22px);font-weight:700;color:var(--gold)}}
.sum-tables{{display:flex;gap:14px}}
.sum-col{{flex:1}}
.sum-col-title{{font-size:10px;letter-spacing:2px;color:#555;text-transform:uppercase;margin-bottom:8px;text-align:center}}
.att-grid{{display:grid;grid-template-columns:1fr 1fr;gap:5px}}
.att-cell{{background:#0e0c08;border:1px solid #2a1a00;border-radius:5px;padding:6px 10px;font-size:clamp(11px,1.1vw,13px);letter-spacing:1px;color:#aaa}}
.att-cell b{{color:var(--gold)}}
.att-empty{{color:#333;grid-column:1/-1;text-align:center}}
.top-list{{display:flex;flex-direction:column;gap:5px}}
.top-cell{{background:#0e0c08;border:1px solid #2a1a00;border-radius:5px;padding:7px 12px;font-size:clamp(11px,1.1vw,13px);letter-spacing:1px;color:#aaa}}
.top-cell b{{color:var(--gold)}}

/* DOWNLOAD BUTTON */
.dl-wrap{{text-align:center;padding:22px 0 36px}}
.dl-btn{{font-family:'Orbitron',monospace;font-size:clamp(11px,1.2vw,13px);letter-spacing:3px;color:var(--text);background:transparent;border:2px solid #333;border-radius:6px;padding:12px 32px;cursor:pointer;text-transform:uppercase;transition:border-color .2s,color .2s}}
.dl-btn:hover{{border-color:var(--orange);color:var(--orange)}}

/* FOOTER */
footer{{text-align:center;padding:20px;color:#333;font-size:12px;letter-spacing:2px;border-top:1px solid var(--border);text-transform:uppercase}}
</style>
</head>
<body>

<header>
  <div class="logo-rp">RP</div>
  <h1><span>RACE</span> PAKISTAN<br>TIMING SYSTEM</h1>
  <div class="logo-nuvex">NUVEX</div>
</header>

<div class="lights-wrap">
  <div class="top-light {top_light}"></div>
  <div class="red-row">{red_lights}</div>
</div>

<div class="status-bar">{status_msg}</div>

<div class="cards">

  <div class="card">
    <div class="ghost">1</div>
    <h2>Player 1</h2>
    {stat_row("Reaction Time", r1["reaction"])}
    {stat_row("Race Time",     r1["race"])}
    {stat_row("Total Time",    r1["total"])}
  </div>

  <div class="card">
    <div class="ghost">2</div>
    <h2>Player 2</h2>
    {stat_row("Reaction Time", r2["reaction"])}
    {stat_row("Race Time",     r2["race"])}
    {stat_row("Total Time",    r2["total"])}
  </div>

</div>

<div class="summary-section">
  {sum_p1}
  {sum_p2}
</div>

<div class="dl-wrap">
  <button class="dl-btn" onclick="downloadCSV()">&#x25BC; Download Attempts</button>
</div>

<footer>© 2025 · NUVEX × Race Pakistan · All Rights Reserved</footer>

<script>
function downloadCSV() {{
  var csv = `{csv_rows_js}`;
  var blob = new Blob([csv], {{type: 'text/csv'}});
  var url  = URL.createObjectURL(blob);
  var a    = document.createElement('a');
  a.href   = url;
  a.download = 'race_pakistan_attempts.csv';
  a.click();
  URL.revokeObjectURL(url);
}}
</script>
</body>
</html>"""


# ══════════════════════════════════════════════
#  WEB SERVER
# ══════════════════════════════════════════════
class RaceHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def do_GET(self):
        with data_lock:
            rs = race_state

        # Determine status message
        if rs == STATE_OBSTACLE:
            msg = "OBSTACLE DETECTED — CLEAR THE TRACK"
        elif rs == STATE_IN_PROGRESS:
            msg = "RACE IN PROGRESS"
        elif rs == STATE_COMPLETE:
            msg = "RACE COMPLETE"
        elif rs == STATE_START:
            msg = "GET READY..."
        elif obstacle_present():
            msg = "OBSTACLE DETECTED — CLEAR THE TRACK"
        else:
            msg = "TRACK CLEAR — READY"

        html = generate_html(msg).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type",   "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(html)))
        self.end_headers()
        self.wfile.write(html)

def start_web_server():
    class QuietServer(HTTPServer):
        def handle_error(self, request, client_address):
            # Suppress noisy ConnectionResetError tracebacks
            # (happens when browser cancels/refreshes a request - harmless)
            import sys
            exc = sys.exc_info()[1]
            if isinstance(exc, (ConnectionResetError, BrokenPipeError)):
                return
            super().handle_error(request, client_address)

    server = QuietServer(("0.0.0.0", 8080), RaceHandler)
    print("🌐  Web server → open  http://192.168.18.195:8080  in any browser")
    server.serve_forever()


# ══════════════════════════════════════════════
#  MAIN RACE LOOP
# ══════════════════════════════════════════════
def main():
    global race_state, lights_off_time

    print("╔══════════════════════════════════════════╗")
    print("║  RACE PAKISTAN – F1 TIMING SYSTEM     ║")
    print("║  Raspberry Pi 4B  |  NUVEX © 2025        ║")
    print("╚══════════════════════════════════════════╝\n")

    # Calibrate sensors at boot
    calibrate_sensors()

    # Start web server thread
    threading.Thread(target=start_web_server, daemon=True).start()

    # Start track threads
    threading.Thread(
        target=track_thread,
        args=(1, REACTION_BTN_P1,
              TRACK1_START, track1_start_clear,
              TRACK1_END,   track1_end_clear, p1),
        daemon=True
    ).start()

    threading.Thread(
        target=track_thread,
        args=(2, REACTION_BTN_P2,
              TRACK2_START, track2_start_clear,
              TRACK2_END,   track2_end_clear, p2),
        daemon=True
    ).start()

    GPIO.output(STATUS_LED, GPIO.LOW)
    print("✅  System ready!\n")
    print("    Press the MASTER BUTTON to start a race.\n")

    try:
        while True:

            # ── IDLE: check for obstacles using track sensors ──
            if obstacle_present():
                with data_lock:
                    race_state = STATE_OBSTACLE
                print("🚧  Obstacle detected — waiting for track to clear...")

                # Wait until all sensors are clear
                while obstacle_present():
                    time.sleep(0.1)

                with data_lock:
                    race_state = STATE_IDLE
                print("✅  Track clear!")

            # ── Wait for master button ─────────────────────────
            # If button pressed while obstacle present → warn and wait
            wait_master_button()

            if obstacle_present():
                print("⚠️   Button pressed but obstacle detected! Clear track first.")
                with data_lock:
                    race_state = STATE_OBSTACLE
                while obstacle_present():
                    time.sleep(0.1)
                with data_lock:
                    race_state = STATE_IDLE
                print("✅  Track clear — press button again to start.")
                continue   # go back and wait for button again

            # ── Track is clear + button pressed → START ────────
            print("━" * 46)
            print("🟢  MASTER button pressed — race sequence starting!")

            with data_lock:
                race_state      = STATE_START
                lights_off_time = None
                reset_player(p1)
                reset_player(p2)

            # ── Lights on one by one ───────────────────────────
            print("🔴  Lights turning on...")
            for i, pin in enumerate(LED_PINS, 1):
                GPIO.output(pin, GPIO.HIGH)
                print(f"    Light {i} ON")
                time.sleep(0.5)

            # ── Hold ON for random 1–3 seconds ─────────────────
            hold_sec = random.uniform(1.0, 3.0)
            print(f"⏳  Lights holding for {hold_sec:.2f}s...")
            time.sleep(hold_sec)

            # ── ALL lights OFF → reaction clock starts ─────────
            for pin in LED_PINS:
                GPIO.output(pin, GPIO.LOW)

            with data_lock:
                lights_off_time = ms()
                race_state      = STATE_IN_PROGRESS

            # !! Obstacle detection is now OFF during the race !!
            print("🏁  LIGHTS OUT — GO GO GO!")

            # ── Wait for both players to finish (max 25 sec) ───
            deadline = ms() + 25_000
            while ms() < deadline:
                with data_lock:
                    done1 = p1["total"] is not None
                    done2 = p2["total"] is not None
                if done1 and done2:
                    break
                time.sleep(0.1)

            # ── Race complete ──────────────────────────────────
            with data_lock:
                race_state = STATE_COMPLETE

            # Save this race to attempt history
            record_attempt()

            print("\n✅  RACE COMPLETE")
            with data_lock:
                print(f"   P1 → Reaction: {p1['reaction']} ms | Race: {p1['race']} ms | Total: {p1['total']} ms")
                print(f"   P2 → Reaction: {p2['reaction']} ms | Race: {p2['race']} ms | Total: {p2['total']} ms")

            time.sleep(5)

            with data_lock:
                race_state = STATE_IDLE

            print("\n🔄  Ready for next race.")
            print("    Press MASTER BUTTON to start.\n")

    except KeyboardInterrupt:
        print("\n🛑  Shutting down...")
    finally:
        for pin in LED_PINS:
            GPIO.output(pin, GPIO.LOW)
        GPIO.output(STATUS_LED, GPIO.LOW)
        GPIO.cleanup()
        print("✅  GPIO cleaned up. Goodbye!")


if __name__ == "__main__":
    main()