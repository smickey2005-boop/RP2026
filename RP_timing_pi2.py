#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════╗
║       RACE PAKISTAN – F1 TIMING SYSTEM                ║
║       Raspberry Pi 4B – Complete Standalone Version      ║
║       NUVEX × Race Pakistan  © 2026                   ║
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
    Start IR  →  GPIO 8   (active LOW = car/obstacle present)
    End IR    →  GPIO 6    (active LOW = car/obstacle present)

  TRACK 2 (Player 2) — also used for obstacle detection:
    Start IR  →  GPIO 23   (active LOW = car/obstacle present)
    End IR    →  GPIO 24   (active LOW = car/obstacle present)

  STATUS LED:
    GPIO 13  →  220Ω  →  LED  →  GND

  PER-TRACK OBSTACLE LEDs:
    Track 1 (Player 1) Obstacle LED  →  GPIO 12  → 220Ω → LED → GND
    Track 2 (Player 2) Obstacle LED  →  GPIO 16  → 220Ω → LED → GND
    (Solid ON while that track has an obstacle, OFF when clear.
     Both turn OFF once a race begins, since obstacle detection
     is paused during the race.)

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
LED_PINS         = [17, 4, 9, 18, 11]  # 5 start lights (LED3→GPIO9, LED5→GPIO11)

MASTER_BUTTON    = 15                  # Race start button

REACTION_BTN_P1  = 27                  # Player 1 reaction button
REACTION_BTN_P2  = 22                  # Player 2 reaction button

# Track sensors — ALSO used for obstacle detection before race
TRACK1_START     = 8                  # Player 1 – start IR sensor
TRACK1_END       = 6                   # Player 1 – end IR sensor
TRACK2_START     = 23                  # Player 2 – start IR sensor
TRACK2_END       = 20                  # Player 2 – end IR sensor

STATUS_LED       = 13                  # Status LED — ON during race

# Per-track obstacle indicator LEDs
TRACK1_OBST_LED  = 12                  # Player 1 – obstacle LED
TRACK2_OBST_LED  = 16                  # Player 2 – obstacle LED

# ══════════════════════════════════════════════
#  RELAY LOGIC LEVEL
# ══════════════════════════════════════════════
# Most relay modules are ACTIVE-LOW: sending GPIO.LOW energizes
# the relay (light ON), GPIO.HIGH de-energizes it (light OFF).
# Set this to True if your relay boards are active-low (most are).
# Set to False if you're driving bare LEDs directly (active-high).
RELAY_ACTIVE_LOW = True

LED_ON  = GPIO.LOW  if RELAY_ACTIVE_LOW else GPIO.HIGH
LED_OFF = GPIO.HIGH if RELAY_ACTIVE_LOW else GPIO.LOW

# ══════════════════════════════════════════════
#  GPIO SETUP
# ══════════════════════════════════════════════
GPIO.setmode(GPIO.BCM)
GPIO.setwarnings(False)

for pin in LED_PINS:
    GPIO.setup(pin, GPIO.OUT)
    GPIO.output(pin, LED_OFF)

GPIO.setup(STATUS_LED, GPIO.OUT)
GPIO.output(STATUS_LED, LED_OFF)   # OFF at boot

GPIO.setup(TRACK1_OBST_LED, GPIO.OUT)
GPIO.output(TRACK1_OBST_LED, LED_OFF)
GPIO.setup(TRACK2_OBST_LED, GPIO.OUT)
GPIO.output(TRACK2_OBST_LED, LED_OFF)

# Master button is NO (Normally Open) — pulls LOW when pressed
GPIO.setup(MASTER_BUTTON, GPIO.IN, pull_up_down=GPIO.PUD_UP)

# Reaction buttons — NC limit switches with external pull resistors
# PUD_OFF: no internal pull, using external resistors only
REACTION_TRIGGERED = GPIO.HIGH   # NC switch opens when launcher fires
GPIO.setup(REACTION_BTN_P1, GPIO.IN, pull_up_down=GPIO.PUD_OFF)
GPIO.setup(REACTION_BTN_P2, GPIO.IN, pull_up_down=GPIO.PUD_OFF)

# IR sensor modules have their own pull-up resistors on the board.
# IR sensors — individual pull settings based on sensor polarity
# IR sensor modules — individual pull settings based on sensor behavior
GPIO.setup(TRACK1_START, GPIO.IN, pull_up_down=GPIO.PUD_OFF)  # has own pull
GPIO.setup(TRACK1_END,   GPIO.IN, pull_up_down=GPIO.PUD_OFF)  # has own pull
GPIO.setup(TRACK2_START, GPIO.IN, pull_up_down=GPIO.PUD_OFF)  # has own pull
GPIO.setup(TRACK2_END,   GPIO.IN, pull_up_down=GPIO.PUD_UP)   # floats LOW — needs pull-up
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
    return (
        GPIO.input(TRACK1_START) != track1_start_clear or
        GPIO.input(TRACK1_END)   != track1_end_clear   or
        GPIO.input(TRACK2_START) != track2_start_clear or
        GPIO.input(TRACK2_END)   != track2_end_clear
    )

def track1_obstacle_present():
    return (
        GPIO.input(TRACK1_START) != track1_start_clear or
        GPIO.input(TRACK1_END)   != track1_end_clear
    )

def track2_obstacle_present():
    return (
        GPIO.input(TRACK2_START) != track2_start_clear or
        GPIO.input(TRACK2_END)   != track2_end_clear
    )

def update_obstacle_leds():
    GPIO.output(TRACK1_OBST_LED, LED_ON if track1_obstacle_present() else LED_OFF)
    GPIO.output(TRACK2_OBST_LED, LED_ON if track2_obstacle_present() else LED_OFF)

def obstacle_led_thread():
    """Updates obstacle LEDs every 50ms in background."""
    while True:
        with data_lock:
            rs = race_state
        if rs in (STATE_IDLE, STATE_OBSTACLE):
            update_obstacle_leds()
        else:
            GPIO.output(TRACK1_OBST_LED, LED_OFF)
            GPIO.output(TRACK2_OBST_LED, LED_OFF)
        time.sleep(0.05)

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

def reset_player(d):
    d["reaction"] = None
    d["race"]     = None
    d["total"]    = None

def record_attempt():
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
        # Launcher is LOADED before lights out (switch CLOSED = HIGH)
        # Player fires after lights out (switch OPENS = LOW) = reaction moment
        # After race, player reloads (switch CLOSES = HIGH) — must ignore this

        # Wait 50ms for pins to settle after lights out
        time.sleep(0.05)

        # Confirm launcher is loaded (HIGH) before listening
        # If not loaded, just wait up to 5s for player to load
        load_wait = ms() + 5_000
        while ms() < load_wait:
            if GPIO.input(reaction_btn) == GPIO.HIGH:
                break
            time.sleep(0.005)

        # Now wait for launcher to FIRE (switch OPENS = LOW)
        deadline = ms() + 30_000
        while ms() < deadline:
            if GPIO.input(reaction_btn) == GPIO.LOW:
                # Debounce — confirm LOW for 5ms
                time.sleep(0.005)
                if GPIO.input(reaction_btn) == GPIO.LOW:
                    press_ms = ms()
                    with data_lock:
                        loff = lights_off_time
                    if loff is not None:
                        reaction_time = max(0, press_ms - loff)
                    print(f"[Player {player_id}] Reaction = {reaction_time} ms")
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
_RP_LOGO    = "data:image/png;base64,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"
_NUVEX_LOGO = "data:image/svg+xml;base64,PHN2ZyB3aWR0aD0iMjMwIiBoZWlnaHQ9IjQ5IiB2aWV3Qm94PSIwIDAgMjMwIDQ5IiBmaWxsPSJub25lIiB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciPgo8cGF0aCBkPSJNMzguMzE1NiAwLjI4MTE3QzM4LjMxNTYgMC4yMDAyMjcgMzguMzQxMSAwLjEzMjA2NCAzOC4zOTIzIDAuMDc2NjgyNEMzOC40NDM0IDAuMDI1NTYwOSAzOC41MTE1IDAgMzguNTk2NyAwSDQ0LjMwNTNDNDQuMzY5MiAwIDQ0LjQyNDYgMC4wMjU1NjA5IDQ0LjQ3NTcgMC4wNzY2ODI0QzQ0LjUyNjggMC4xMjc4MDQgNDQuNTUyNCAwLjE5NTk2NyA0NC41NTI0IDAuMjgxMTdWMzAuOTQ5OEM0NC41NTI0IDMxLjAxMzcgNDQuNTI2OCAzMS4wNjkxIDQ0LjQ3NTcgMzEuMTIwMkM0NC40MjQ2IDMxLjE3MTMgNDQuMzY1IDMxLjE5NjkgNDQuMzA1MyAzMS4xOTY5SDMyLjYzNjhDMzAuMjQ2OSAzMS4xMzMgMjguMzYzOSAzMC4xMjc2IDI2Ljk4NzkgMjguMTcyMkwxMi40ODIyIDYuNTUyMDdDMTEuODc3MiA1Ljc2Mzk1IDExLjEzMTcgNS4zNjc3NiAxMC4yMzcxIDUuMzY3NzZINi4yNDUzNFYzMC45NDk4QzYuMjQ1MzQgMzEuMDEzNyA2LjIxOTc4IDMxLjA2OTEgNi4xNjg2NiAzMS4xMjAyQzYuMTE3NTQgMzEuMTcxMyA2LjA0OTM4IDMxLjE5NjkgNS45NjQxOCAzMS4xOTY5SDAuMjU1NjA3QzAuMTkxNzA1IDMxLjE5NjkgMC4xMzYzMjMgMzEuMTcxMyAwLjA4NTIwMTIgMzEuMTIwMkMwLjAzNDA3OTcgMzEuMDY5MSAwLjAwODUxODgyIDMxLjAwOTQgMC4wMDg1MTg4MiAzMC45NDk4VjAuMjgxMTdDMC4wMDg1MTg4MiAwLjIwMDIyNyAwLjAzNDA3OTcgMC4xMzIwNjQgMC4wODUyMDEyIDAuMDc2NjgyNEMwLjEzNjMyMyAwLjAyNTU2MDkgMC4xOTU5NjUgMCAwLjI1NTYwNyAwSDExLjkyNDFDMTQuMzE0IDAuMDg1MjAyNSAxNi4xOTcgMS4xMDMzNyAxNy41Njg4IDMuMDU4NzdMMzIuMDc0NSAyNC42NDkxQzMyLjY1ODEgMjUuNDU4NSAzMy40MDM2IDI1Ljg2NzUgMzQuMzE5NiAyNS44Njc1SDM4LjMxMTNWMC4yODU0MjhMMzguMzE1NiAwLjI4MTE3WiIgZmlsbD0id2hpdGUiLz4KPHBhdGggZD0iTTkyLjEwODIgMjIuMzEwM0M5Mi4xMDgyIDI0Ljc2NDEgOTEuMjM5MSAyNi44NjAxIDg5LjUwNTMgMjguNTk4MkM4Ny43NjcxIDMwLjMzNjMgODUuNjYyNiAzMS4yMDEyIDgzLjE4NzUgMzEuMjAxMkg1Ni40NTA5QzUzLjk5NzEgMzEuMjAxMiA1MS44OTY5IDMwLjMzMjEgNTAuMTUwMiAyOC41OTgyQzQ4LjQwMzYgMjYuODYwMSA0Ny41MzAyIDI0Ljc2NDEgNDcuNTMwMiAyMi4zMTAzVjAuMjg1NDI4QzQ3LjUzMDIgMC4yMDQ0ODUgNDcuNTU1OCAwLjEzNjMyNSA0Ny42MDY5IDAuMDgwOTQzNEM0Ny42NTggMC4wMjk4MjE5IDQ3LjcyNjIgMC4wMDQyNjEwMyA0Ny44MTE0IDAuMDA0MjYxMDNINTMuNTJDNTMuNTgzOSAwLjAwNDI2MTAzIDUzLjYzOTMgMC4wMjk4MjE5IDUzLjY5MDQgMC4wODA5NDM0QzUzLjc0MTUgMC4xMzIwNjUgNTMuNzY3MSAwLjIwMDIyNSA1My43NjcxIDAuMjg1NDI4VjIzLjE4MzZDNTMuNzY3MSAyMy45MzM0IDU0LjAzMTIgMjQuNTY4MSA1NC41NjM3IDI1LjA4NzlDNTUuMDkyIDI1LjYwNzYgNTUuNzIyNCAyNS44Njc1IDU2LjQ1MDkgMjUuODY3NUg4My4xODc1QzgzLjkzNzMgMjUuODY3NSA4NC41NzIgMjUuNjA3NiA4NS4wOTE4IDI1LjA4NzlDODUuNjExNSAyNC41NjgxIDg1Ljg3MTQgMjMuOTMzNCA4NS44NzE0IDIzLjE4MzZWMC4yODU0MjhDODUuODcxNCAwLjIwNDQ4NSA4NS44OTY5IDAuMTM2MzI1IDg1Ljk0OCAwLjA4MDk0MzRDODUuOTk5MiAwLjAyOTgyMTkgODYuMDY3MyAwLjAwNDI2MTAzIDg2LjE1MjUgMC4wMDQyNjEwM0g5MS44MzEzQzkxLjkxMjIgMC4wMDQyNjEwMyA5MS45ODA0IDAuMDI5ODIxOSA5Mi4wMzU4IDAuMDgwOTQzNEM5Mi4wODY5IDAuMTMyMDY1IDkyLjExMjUgMC4yMDAyMjUgOTIuMTEyNSAwLjI4NTQyOFYyMi4zMTAzSDkyLjEwODJaIiBmaWxsPSJ3aGl0ZSIvPgo8cGF0aCBkPSJNMTA4LjYxMiAyOC44NTgxQzEwNi45NjggMzAuNDE3MyAxMDUuMDc2IDMxLjE5NjkgMTAyLjkzMyAzMS4xOTY5SDk0LjUwMjRDOTQuNDM4NSAzMS4xOTY5IDk0LjM4MzEgMzEuMTcxMyA5NC4zMzIgMzEuMTIwMkM5NC4yODA5IDMxLjA2OTEgOTQuMjU1MyAzMS4wMDk0IDk0LjI1NTMgMzAuOTQ5OFYwLjI4MTE3Qzk0LjI1NTMgMC4yMDAyMjcgOTQuMjgwOSAwLjEzMjA2NCA5NC4zMzIgMC4wNzY2ODI0Qzk0LjM4MzEgMC4wMjU1NjA5IDk0LjQ0MjcgMCA5NC41MDI0IDBIMTAwLjIxMUMxMDAuMjk2IDAgMTAwLjM2IDAuMDI1NTYwOSAxMDAuNDE1IDAuMDc2NjgyNEMxMDAuNDY3IDAuMTI3ODA0IDEwMC40OTIgMC4xOTU5NjcgMTAwLjQ5MiAwLjI4MTE3VjIxLjg4ODVDMTAwLjQ5MiAyMy4yMDkyIDEwMi4xNTQgMjMuNzkyOCAxMDIuOTggMjIuNzYxOEwxMjEuMTY3IDAuMDkzNzIzM0MxMjEuMjMgMC4wMjk4MjE0IDEyMS4yOSAwIDEyMS4zNTQgMEgxNDEuNzA5QzE0MS44MzIgMCAxNDEuOTE4IDAuMDUxMTIyNyAxNDEuOTYgMC4xNTc2MjZDMTQyLjAwMyAwLjI1OTg2OSAxNDEuOTgxIDAuMzY2MzcgMTQxLjg5NiAwLjQ2ODYxM0wxMDguNjEyIDI4Ljg1ODFaIiBmaWxsPSJ3aGl0ZSIvPgo8cGF0aCBkPSJNMTQ1LjEyNSAxMi45MTY3SDE3OC43MjVDMTc4LjgwNiAxMi45MTY3IDE3OC44NzQgMTIuOTQyMyAxNzguOTMgMTIuOTkzNEMxNzguOTgxIDEzLjA0NDUgMTc5LjAwNiAxMy4xMTI3IDE3OS4wMDYgMTMuMTk3OVYxOC4wMDMzQzE3OS4wMDYgMTguMDg4NSAxNzguOTgxIDE4LjE1MjQgMTc4LjkzIDE4LjIwNzhDMTc4Ljg3OCAxOC4yNjMyIDE3OC44MSAxOC4yODQ1IDE3OC43MjUgMTguMjg0NUgxNDUuMTI1VjIzLjE4MzZDMTQ1LjEyNSAyMy45MTIxIDE0NS4zODUgMjQuNTQyNiAxNDUuOTA1IDI1LjA3MDhDMTQ2LjQyNSAyNS41OTkxIDE0Ny4wNiAyNS44Njc1IDE0Ny44MDkgMjUuODY3NUgxODMuMTlDMTgzLjI3MSAyNS44Njc1IDE4My4zMzkgMjUuODkzIDE4My4zOTQgMjUuOTQ0MkMxODMuNDQ1IDI1Ljk5NTMgMTgzLjQ3MSAyNi4wNTQ5IDE4My40NzEgMjYuMTE0NlYzMC45MkMxODMuNDcxIDMxLjAwNTIgMTgzLjQ0NSAzMS4wNjkxIDE4My4zOTQgMzEuMTI0NUMxODMuMzQzIDMxLjE3NTYgMTgzLjI3NSAzMS4yMDEyIDE4My4xOSAzMS4yMDEySDE0Ny44MDlDMTQ1LjMzNCAzMS4yMDEyIDE0My4yMyAzMC4zMzIxIDE0MS40OTIgMjguNTk4MkMxMzkuNzUzIDI2Ljg2MDEgMTM4Ljg4OSAyNC43NTU2IDEzOC44ODkgMjIuMjgwNVY4LjkyOTIyQzEzOC44ODkgNi40NTQwOSAxMzkuNzU4IDQuMzQ5NTkgMTQxLjQ5MiAyLjYxMTQ2QzE0My4yMyAwLjg3MzMyOCAxNDUuMzM0IDAuMDA4NTIyMDcgMTQ3LjgwOSAwLjAwODUyMjA3SDE4My4xOUMxODMuMjcxIDAuMDA4NTIyMDcgMTgzLjMzOSAwLjAzNDA4MjkgMTgzLjM5NCAwLjA4NTIwNDRDMTgzLjQ0NSAwLjEzNjMyNiAxODMuNDcxIDAuMjA0NDg2IDE4My40NzEgMC4yODk2ODlWNS4wOTUxMUMxODMuNDcxIDUuMTU5MDEgMTgzLjQ0NSA1LjIxNDM5IDE4My4zOTQgNS4yNjU1MUMxODMuMzQzIDUuMzE2NjQgMTgzLjI3NSA1LjM0MjIgMTgzLjE5IDUuMzQyMkgxNDcuODA5QzE0Ny4wNiA1LjM0MjIgMTQ2LjQyNSA1LjYwNjMzIDE0NS45MDUgNi4xMzg4NEMxNDUuMzg1IDYuNjcxMzYgMTQ1LjEyNSA3LjI5NzYgMTQ1LjEyNSA4LjAyNjA4VjEyLjkyNTJWMTIuOTE2N1oiIGZpbGw9IndoaXRlIi8+CjxwYXRoIGQ9Ik0yMjEuMDQ1IDUuMzM3OTRDMjIwLjIzNiA1LjMzNzk0IDIxOS41MzcgNS42NzAyMyAyMTguOTUzIDYuMzM0ODFMMjExLjQzNCAxNS42MDA2TDIxOC45NTMgMjQuODY2M0MyMTkuNTM3IDI1LjUwOTYgMjIwLjIzMiAyNS44MzM0IDIyMS4wNDUgMjUuODMzNEgyMjkuNzQ5QzIyOS44MTMgMjUuODMzNCAyMjkuODY4IDI1Ljg1OSAyMjkuOTE5IDI1LjkxMDFDMjI5Ljk3IDI1Ljk2MTIgMjI5Ljk5NiAyNi4wMjk0IDIyOS45OTYgMjYuMTE0NlYzMC45MkMyMjkuOTk2IDMxLjAwNTIgMjI5Ljk3IDMxLjA2OTEgMjI5LjkxOSAzMS4xMjQ1QzIyOS44NjggMzEuMTc1NiAyMjkuODA4IDMxLjIwMTIgMjI5Ljc0OSAzMS4yMDEySDIyMC41MTNDMjE4LjEwMSAzMS4yMDEyIDIxNi4wMjIgMzAuMjEyOCAyMTQuMjcyIDI4LjIzNjFMMjA3LjcxOSAyMC4xNTQ3TDIwMS4xNjcgMjguMjM2MUMxOTkuNDIxIDMwLjIxMjggMTk3LjM0MiAzMS4yMDEyIDE5NC45MjYgMzEuMjAxMkgxODUuNjlDMTg1LjYyNiAzMS4yMDEyIDE4NS41NzEgMzEuMTc1NiAxODUuNTIgMzEuMTI0NUMxODUuNDY5IDMxLjA3MzQgMTg1LjQ0MyAzMS4wMDUyIDE4NS40NDMgMzAuOTJWMjYuMTE0NkMxODUuNDQzIDI2LjAzMzYgMTg1LjQ2OSAyNS45NjU1IDE4NS41MiAyNS45MTAxQzE4NS41NzEgMjUuODU0NyAxODUuNjMxIDI1LjgzMzQgMTg1LjY5IDI1LjgzMzRIMTk0LjM5NEMxOTUuMjAzIDI1LjgzMzQgMTk1LjkwMiAyNS41MDk2IDE5Ni40ODYgMjQuODY2M0wyMDQuMDA1IDE1LjYwMDZMMTk2LjQ4NiA2LjMzNDgxQzE5NS45MDIgNS42NzAyMyAxOTUuMjE2IDUuMzM3OTQgMTk0LjQyOCA1LjMzNzk0SDE4NS42OUMxODUuNjI2IDUuMzM3OTQgMTg1LjU3MSA1LjMxMjM4IDE4NS41MiA1LjI2MTI1QzE4NS40NjkgNS4yMTAxMyAxODUuNDQzIDUuMTUwNDkgMTg1LjQ0MyA1LjA5MDg1VjAuMjU1NjA3QzE4NS40NDMgMC4xOTE3MDUgMTg1LjQ2OSAwLjEzNjMyNiAxODUuNTIgMC4wODUyMDQ0QzE4NS41NzEgMC4wMzQwODI5IDE4NS42MzEgMC4wMDg1MjIwNyAxODUuNjkgMC4wMDg1MjIwN0gxOTQuOTI2QzE5Ny4zMzggMC4wMDg1MjIwNyAxOTkuNDE3IDAuOTk2ODcxIDIwMS4xNjcgMi45NzM1N0wyMDcuNzE5IDExLjA1NUwyMTQuMzAxIDIuOTczNTdDMjE2LjAyNyAwLjk5Njg3MSAyMTguMDk3IDAuMDA4NTIyMDcgMjIwLjUwOCAwLjAwODUyMjA3SDIyOS43NDRDMjI5LjgwOCAwLjAwODUyMjA3IDIyOS44NjQgMC4wMzQwODI5IDIyOS45MTUgMC4wODUyMDQ0QzIyOS45NjYgMC4xMzYzMjYgMjI5Ljk5MSAwLjE5NTk2NSAyMjkuOTkxIDAuMjU1NjA3VjUuMDkwODVDMjI5Ljk5MSA1LjE1NDc1IDIyOS45NjYgNS4yMTAxMyAyMjkuOTE1IDUuMjYxMjVDMjI5Ljg2NCA1LjMxMjM4IDIyOS44MDQgNS4zMzc5NCAyMjkuNzQ0IDUuMzM3OTRIMjIxLjA0MUgyMjEuMDQ1WiIgZmlsbD0id2hpdGUiLz4KPHBhdGggZD0iTTAuMDA0MjYxMDQgMzUuNTE2N0MwLjAwNDI2MTA0IDM1LjQ4MjYgMC4wMTI3ODEyIDM1LjQ1NyAwLjAzNDA4MTggMzUuNDMxNUMwLjA1NTM4MjQgMzUuNDEwMiAwLjA4MDk0MjQgMzUuNDAxNiAwLjEwNjUwMyAzNS40MDE2SDE4LjEwOThDMTguMTM1NCAzNS40MDE2IDE4LjE1NjcgMzUuNDE0NCAxOC4xNzggMzUuNDMxNUMxOC4xOTkzIDM1LjQ1MjggMTguMjEyIDM1LjQ4MjYgMTguMjEyIDM1LjUxNjdWMzcuNDgwNkMxOC4yMTIgMzcuNTA2MSAxOC4xOTkzIDM3LjUyNzQgMTguMTc4IDM3LjU0ODdDMTguMTU2NyAzNy41NyAxOC4xMzU0IDM3LjU4MjggMTguMTA5OCAzNy41ODI4SDEwLjM4MTlWNDguMDM3MkMxMC4zODE5IDQ4LjA3MTIgMTAuMzY5MSA0OC4wOTY4IDEwLjM0NzggNDguMTIyNEMxMC4zMjY1IDQ4LjE0MzcgMTAuMzA1MiA0OC4xNTIyIDEwLjI3OTcgNDguMTUyMkg3Ljk0NTE0QzcuOTExMDYgNDguMTUyMiA3Ljg4NTQ5IDQ4LjEzOTQgNy44NTk5MyA0OC4xMjI0QzcuODM4NjMgNDguMTAxMSA3LjgzMDExIDQ4LjA3NTUgNy44MzAxMSA0OC4wMzcyVjM3LjU4MjhIMC4xMDIyNDJDMC4wNzY2ODE0IDM3LjU4MjggMC4wNTUzODE1IDM3LjU3IDAuMDI5ODIwOCAzNy41NDg3QzAuMDA4NTIwMTIgMzcuNTI3NCAwIDM3LjUwNjEgMCAzNy40ODA2VjM1LjUxNjdIMC4wMDQyNjEwNFoiIGZpbGw9IndoaXRlIi8+CjxwYXRoIGQ9Ik0zOS45MDQ2IDQ4LjA0OTlDMzkuOTA0NiA0OC4wNzU1IDM5Ljg5MTggNDguMDk2OCAzOS44NzA1IDQ4LjEyMjRDMzkuODQ5MiA0OC4xNDM3IDM5LjgyMzcgNDguMTUyMiAzOS43ODk2IDQ4LjE1MjJIMzcuNDY3OEMzNy40MzM3IDQ4LjE1MjIgMzcuNDA4MiA0OC4xMzk0IDM3LjM4MjYgNDguMTIyNEMzNy4zNjEzIDQ4LjEwMTEgMzcuMzUyOCA0OC4wNzU1IDM3LjM1MjggNDguMDQ5OVY0Mi44NzM5SDI0LjIzMTZWNDguMDQ5OUMyNC4yMzE2IDQ4LjA3NTUgMjQuMjE4OCA0OC4wOTY4IDI0LjE5NzUgNDguMTIyNEMyNC4xNzYyIDQ4LjE0MzcgMjQuMTU0OSA0OC4xNTIyIDI0LjEyOTQgNDguMTUyMkgyMS43OTQ4QzIxLjc2MDcgNDguMTUyMiAyMS43MzUyIDQ4LjEzOTQgMjEuNzA5NiA0OC4xMjI0QzIxLjY4ODMgNDguMTAxMSAyMS42Nzk4IDQ4LjA3NTUgMjEuNjc5OCA0OC4wNDk5VjM1LjUxNjdDMjEuNjc5OCAzNS40ODI2IDIxLjY4ODMgMzUuNDU3IDIxLjcwOTYgMzUuNDMxNUMyMS43MzA5IDM1LjQxMDIgMjEuNzU2NSAzNS40MDE2IDIxLjc5NDggMzUuNDAxNkgyNC4xMjk0QzI0LjE1NDkgMzUuNDAxNiAyNC4xNzYyIDM1LjQxNDQgMjQuMTk3NSAzNS40MzE1QzI0LjIxODggMzUuNDUyOCAyNC4yMzE2IDM1LjQ4MjYgMjQuMjMxNiAzNS41MTY3VjQwLjY3OTlIMzcuMzUyOFYzNS41MTY3QzM3LjM1MjggMzUuNDgyNiAzNy4zNjEzIDM1LjQ1NyAzNy4zODI2IDM1LjQzMTVDMzcuNDAzOSAzNS40MTAyIDM3LjQyOTUgMzUuNDAxNiAzNy40Njc4IDM1LjQwMTZIMzkuNzg5NkMzOS44MjM3IDM1LjQwMTYgMzkuODQ5MiAzNS40MTQ0IDM5Ljg3MDUgMzUuNDMxNUMzOS44OTE4IDM1LjQ1MjggMzkuOTA0NiAzNS40ODI2IDM5LjkwNDYgMzUuNTE2N1Y0OC4wNDk5WiIgZmlsbD0id2hpdGUiLz4KPHBhdGggZD0iTTQ2LjA1MiA0OC4wNDk5QzQ2LjA1MiA0OC4wNzU1IDQ2LjAzOTIgNDguMDk2OCA0Ni4wMTc5IDQ4LjEyMjRDNDUuOTk2NiA0OC4xNDM3IDQ1Ljk3MSA0OC4xNTIyIDQ1LjkzNjkgNDguMTUyMkg0My42MDI0QzQzLjU3NjggNDguMTUyMiA0My41NTU1IDQ4LjEzOTQgNDMuNTMgNDguMTIyNEM0My41MDg3IDQ4LjEwMTEgNDMuNTAwMSA0OC4wNzU1IDQzLjUwMDEgNDguMDQ5OVYzNS41MTY3QzQzLjUwMDEgMzUuNDgyNiA0My41MDg3IDM1LjQ1NyA0My41MyAzNS40MzE1QzQzLjU1MTMgMzUuNDEwMiA0My41NzY4IDM1LjQwMTYgNDMuNjAyNCAzNS40MDE2SDQ1LjkzNjlDNDUuOTcxIDM1LjQwMTYgNDUuOTk2NiAzNS40MTQ0IDQ2LjAxNzkgMzUuNDMxNUM0Ni4wMzkyIDM1LjQ1MjggNDYuMDUyIDM1LjQ4MjYgNDYuMDUyIDM1LjUxNjdWNDguMDQ5OVoiIGZpbGw9IndoaXRlIi8+CjxwYXRoIGQ9Ik02NS4zMDM1IDM1LjUxNjdDNjUuMzAzNSAzNS40ODI2IDY1LjMxNjMgMzUuNDU3IDY1LjMzNzYgMzUuNDMxNUM2NS4zNTg5IDM1LjQwNTkgNjUuMzg0NCAzNS40MDE2IDY1LjQxODUgMzUuNDAxNkg2Ny43NTNDNjcuNzc4NiAzNS40MDE2IDY3Ljc5OTkgMzUuNDE0NCA2Ny44MjU1IDM1LjQzMTVDNjcuODQ2OCAzNS40NTI4IDY3Ljg1NTMgMzUuNDgyNiA2Ny44NTUzIDM1LjUxNjdWNDguMDQ5OUM2Ny44NTUzIDQ4LjA3NTUgNjcuODQyNSA0OC4wOTY4IDY3LjgyNTUgNDguMTIyNEM2Ny44MDQyIDQ4LjE0MzcgNjcuNzc4NiA0OC4xNTIyIDY3Ljc1MyA0OC4xNTIySDYyLjk4NkM2Mi4wMTA0IDQ4LjEyNjYgNjEuMjM5MyA0Ny43MTM0IDYwLjY3NyA0Ni45MTY4TDU0Ljc0NjkgMzguMDgxM0M1NC40OTk4IDM3Ljc1NzUgNTQuMTkzMSAzNy41OTU2IDUzLjgzMSAzNy41OTU2SDUyLjE5OTNWNDguMDQ5OUM1Mi4xOTkzIDQ4LjA3NTUgNTIuMTg2NSA0OC4wOTY4IDUyLjE2OTUgNDguMTIyNEM1Mi4xNDgyIDQ4LjE0MzcgNTIuMTIyNiA0OC4xNTIyIDUyLjA4NDMgNDguMTUyMkg0OS43NDk4QzQ5LjcyNDIgNDguMTUyMiA0OS43MDI5IDQ4LjEzOTQgNDkuNjgxNiA0OC4xMjI0QzQ5LjY2MDMgNDguMTA1MyA0OS42NDc1IDQ4LjA3NTUgNDkuNjQ3NSA0OC4wNDk5VjM1LjUxNjdDNDkuNjQ3NSAzNS40ODI2IDQ5LjY2MDMgMzUuNDU3IDQ5LjY4MTYgMzUuNDMxNUM0OS43MDI5IDM1LjQwNTkgNDkuNzI0MiAzNS40MDE2IDQ5Ljc0OTggMzUuNDAxNkg1NC41MTY4QzU1LjQ5MjQgMzUuNDM1NyA1Ni4yNjM1IDM1Ljg1MzIgNTYuODI1OCAzNi42NDk5TDYyLjc1NTkgNDUuNDcyNkM2Mi45OTQ1IDQ1LjgwNDkgNjMuMzAxMiA0NS45NzEgNjMuNjcxOCA0NS45NzFINjUuMzAzNVYzNS41MTY3WiIgZmlsbD0id2hpdGUiLz4KPHBhdGggZD0iTTg5Ljk2NTMgNDcuOTk4OEM4OS45ODI0IDQ4LjAzMjkgODkuOTc4MSA0OC4wNjcgODkuOTU2OCA0OC4xMDExQzg5LjkzNTUgNDguMTM1MSA4OS45MSA0OC4xNTIyIDg5Ljg3NTkgNDguMTUyMkg4Ny4yODU3Qzg3LjI0MzEgNDguMTUyMiA4Ny4yMDkgNDguMTMwOSA4Ny4xODM1IDQ4LjA4ODNMODUuMjgzNSA0NC4wODM4Qzg1LjEyMTYgNDMuNzY4NSA4NC44OTU4IDQzLjUxNzIgODQuNjAxOCA0My4zMjk3Qzg0LjMwNzkgNDMuMTQyMyA4My45ODg0IDQzLjA0ODYgODMuNjM5MSA0My4wNDg2SDc0LjAxMTJWNDguMDMyOUM3NC4wMTEyIDQ4LjA2NyA3My45OTg0IDQ4LjA5MjYgNzMuOTc3MSA0OC4xMTgxQzczLjk1NTggNDguMTM5NCA3My45MzAyIDQ4LjE0NzkgNzMuODk2MSA0OC4xNDc5SDcxLjU3NDRDNzEuNTQwMyA0OC4xNDc5IDcxLjUxNDcgNDguMTM1MiA3MS40ODkyIDQ4LjExODFDNzEuNDY3OSA0OC4wOTY4IDcxLjQ1OTQgNDguMDcxMiA3MS40NTk0IDQ4LjAzMjlWMzUuNTEyNEM3MS40NTk0IDM1LjQ3ODMgNzEuNDY3OSAzNS40NTI4IDcxLjQ4OTIgMzUuNDI3MkM3MS41MTA1IDM1LjQwNTkgNzEuNTM2IDM1LjM5NzQgNzEuNTc0NCAzNS4zOTc0SDczLjg5NjFDNzMuOTMwMiAzNS4zOTc0IDczLjk1NTggMzUuNDEwMiA3My45NzcxIDM1LjQyNzJDNzMuOTk4NCAzNS40NDg1IDc0LjAxMTIgMzUuNDc4MyA3NC4wMTEyIDM1LjUxMjRWNDAuODY3NEg4My42MzkxQzgzLjk4ODQgNDAuODY3NCA4NC4zMDc5IDQwLjc3MzcgODQuNjAxOCA0MC41ODYyQzg0Ljg5NTggNDAuMzk4OCA4NS4xMjE2IDQwLjE0MzIgODUuMjgzNSAzOS44MTk0TDg3LjE4MzUgMzUuODI3N0M4Ny4yMDkgMzUuNzg1IDg3LjI0MzEgMzUuNzYzOCA4Ny4yODU3IDM1Ljc2MzhIODkuODc1OUM4OS45MSAzNS43NjM4IDg5LjkzNTUgMzUuNzc2NSA4OS45NTY4IDM1LjgwNjNDODkuOTc4MSAzNS44MzYyIDg5Ljk4MjQgMzUuODc0NSA4OS45NjUzIDM1LjkxMjhMODcuNzU4NiA0MC41NTIxQzg3LjQ5NDUgNDEuMDk3NCA4Ny4xNDA5IDQxLjU1NzUgODYuNzAyMSA0MS45NDA5Qzg3LjE0NTEgNDIuMzMyOSA4Ny40OTg3IDQyLjgwMTUgODcuNzU4NiA0My4zNDI1TDg5Ljk2NTMgNDcuOTgxOFY0Ny45OTg4WiIgZmlsbD0id2hpdGUiLz4KPHBhdGggZD0iTTEwOS4xMTUgNDguMDQ5OUMxMDkuMTE1IDQ4LjA3NTUgMTA5LjEwMiA0OC4wOTY4IDEwOS4wODEgNDguMTIyNEMxMDkuMDU5IDQ4LjE0MzcgMTA5LjAzNCA0OC4xNTIyIDEwOSA0OC4xNTIySDEwNi42NjVDMTA2LjYzOSA0OC4xNTIyIDEwNi42MTggNDguMTM5NCAxMDYuNTkzIDQ4LjEyMjRDMTA2LjU3MSA0OC4xMDExIDEwNi41NjMgNDguMDc1NSAxMDYuNTYzIDQ4LjA0OTlWMzUuNTE2N0MxMDYuNTYzIDM1LjQ4MjYgMTA2LjU3MSAzNS40NTcgMTA2LjU5MyAzNS40MzE1QzEwNi42MTQgMzUuNDEwMiAxMDYuNjM5IDM1LjQwMTYgMTA2LjY2NSAzNS40MDE2SDEwOUMxMDkuMDM0IDM1LjQwMTYgMTA5LjA1OSAzNS40MTQ0IDEwOS4wODEgMzUuNDMxNUMxMDkuMTAyIDM1LjQ1MjggMTA5LjExNSAzNS40ODI2IDEwOS4xMTUgMzUuNTE2N1Y0OC4wNDk5WiIgZmlsbD0id2hpdGUiLz4KPHBhdGggZD0iTTEyOC4zNjYgMzUuNTE2N0MxMjguMzY2IDM1LjQ4MjYgMTI4LjM3OSAzNS40NTcgMTI4LjQgMzUuNDMxNUMxMjguNDIyIDM1LjQwNTkgMTI4LjQ0NyAzNS40MDE2IDEyOC40ODEgMzUuNDAxNkgxMzAuODE2QzEzMC44NDEgMzUuNDAxNiAxMzAuODYzIDM1LjQxNDQgMTMwLjg4OCAzNS40MzE1QzEzMC45MDkgMzUuNDUyOCAxMzAuOTE4IDM1LjQ4MjYgMTMwLjkxOCAzNS41MTY3VjQ4LjA0OTlDMTMwLjkxOCA0OC4wNzU1IDEzMC45MDUgNDguMDk2OCAxMzAuODg4IDQ4LjEyMjRDMTMwLjg2NyA0OC4xNDM3IDEzMC44NDEgNDguMTUyMiAxMzAuODE2IDQ4LjE1MjJIMTI2LjA0OUMxMjUuMDczIDQ4LjEyNjYgMTI0LjMwMiA0Ny43MTM0IDEyMy43NCA0Ni45MTY4TDExNy44MSAzOC4wODEzQzExNy41NjIgMzcuNzU3NSAxMTcuMjU2IDM3LjU5NTYgMTE2Ljg5NCAzNy41OTU2SDExNS4yNjJWNDguMDQ5OUMxMTUuMjYyIDQ4LjA3NTUgMTE1LjI0OSA0OC4wOTY4IDExNS4yMzIgNDguMTIyNEMxMTUuMjExIDQ4LjE0MzcgMTE1LjE4NSA0OC4xNTIyIDExNS4xNDcgNDguMTUyMkgxMTIuODEyQzExMi43ODcgNDguMTUyMiAxMTIuNzY2IDQ4LjEzOTQgMTEyLjc0NCA0OC4xMjI0QzExMi43MjMgNDguMTA1MyAxMTIuNzEgNDguMDc1NSAxMTIuNzEgNDguMDQ5OVYzNS41MTY3QzExMi43MSAzNS40ODI2IDExMi43MjMgMzUuNDU3IDExMi43NDQgMzUuNDMxNUMxMTIuNzY2IDM1LjQwNTkgMTEyLjc4NyAzNS40MDE2IDExMi44MTIgMzUuNDAxNkgxMTcuNTc5QzExOC41NTUgMzUuNDM1NyAxMTkuMzI2IDM1Ljg1MzIgMTE5Ljg4OCAzNi42NDk5TDEyNS44MTkgNDUuNDcyNkMxMjYuMDU3IDQ1LjgwNDkgMTI2LjM2NCA0NS45NzEgMTI2LjczNCA0NS45NzFIMTI4LjM2NlYzNS41MTY3WiIgZmlsbD0id2hpdGUiLz4KPHBhdGggZD0iTTEzNy4wNjUgNDEuNzc5SDE1MC44MDhDMTUwLjgzNCA0MS43NzkgMTUwLjg1NSA0MS43OTE4IDE1MC44NzcgNDEuODA4OUMxNTAuODk4IDQxLjgzMDIgMTUwLjkxMSA0MS44NiAxNTAuOTExIDQxLjg5NDFWNDMuODU4QzE1MC45MTEgNDMuODgzNSAxNTAuODk4IDQzLjkwNDkgMTUwLjg3NyA0My45MjYxQzE1MC44NTUgNDMuOTQ3NSAxNTAuODM0IDQzLjk2MDIgMTUwLjgwOCA0My45NjAySDEzNy4wNjVWNDguMDQxNEMxMzcuMDY1IDQ4LjA3NTUgMTM3LjA1MyA0OC4xMDExIDEzNy4wMzEgNDguMTI2NkMxMzcuMDEgNDguMTQ3OSAxMzYuOTg5IDQ4LjE1NjUgMTM2Ljk2MyA0OC4xNTY1SDEzNC42MjlDMTM0LjU5NCA0OC4xNTY1IDEzNC41NjkgNDguMTQzNyAxMzQuNTQzIDQ4LjEyNjZDMTM0LjUyMiA0OC4xMDUzIDEzNC41MTMgNDguMDc5OCAxMzQuNTEzIDQ4LjA0MTRWMzguNjgxOUMxMzQuNTEzIDM3Ljc4MyAxMzQuODMzIDM3LjAwNzcgMTM1LjQ3NiAzNi4zNjg3QzEzNi4xMiAzNS43MjU0IDEzNi44OTUgMzUuNDA1OSAxMzcuODAyIDM1LjQwNTlIMTUyLjYxOUMxNTIuNjUzIDM1LjQwNTkgMTUyLjY3OSAzNS40MTg3IDE1Mi43IDM1LjQzNTdDMTUyLjcyMSAzNS40NTcgMTUyLjczNCAzNS40ODY4IDE1Mi43MzQgMzUuNTIwOVYzNy40ODQ4QzE1Mi43MzQgMzcuNTEwNCAxNTIuNzIxIDM3LjUzMTcgMTUyLjcgMzcuNTUzQzE1Mi42NzkgMzcuNTc0MyAxNTIuNjUzIDM3LjU4NzEgMTUyLjYxOSAzNy41ODcxSDEzOC4xNkMxMzcuODYyIDM3LjU4NzEgMTM3LjYwNiAzNy42OTM2IDEzNy4zODkgMzcuOTEwOEMxMzcuMTcyIDM4LjEyODEgMTM3LjA2NSAzOC4zODM3IDEzNy4wNjUgMzguNjgxOVY0MS43NzlaIiBmaWxsPSJ3aGl0ZSIvPgo8cGF0aCBkPSJNMTU4Ljc0OSA0OC4wNDk5QzE1OC43NDkgNDguMDc1NSAxNTguNzM3IDQ4LjA5NjggMTU4LjcyIDQ4LjEyMjRDMTU4LjY5OCA0OC4xNDM3IDE1OC42NzMgNDguMTUyMiAxNTguNjM0IDQ4LjE1MjJIMTU2LjNDMTU2LjI3NCA0OC4xNTIyIDE1Ni4yNTMgNDguMTM5NCAxNTYuMjMyIDQ4LjEyMjRDMTU2LjIxIDQ4LjEwNTMgMTU2LjE5OCA0OC4wNzU1IDE1Ni4xOTggNDguMDQ5OVYzNS41MTY3QzE1Ni4xOTggMzUuNDgyNiAxNTYuMjEgMzUuNDU3IDE1Ni4yMzIgMzUuNDMxNUMxNTYuMjUzIDM1LjQwNTkgMTU2LjI3NCAzNS40MDE2IDE1Ni4zIDM1LjQwMTZIMTU4LjYzNEMxNTguNjY4IDM1LjQwMTYgMTU4LjY5NCAzNS40MTQ0IDE1OC43MiAzNS40MzE1QzE1OC43NDEgMzUuNDUyOCAxNTguNzQ5IDM1LjQ4MjYgMTU4Ljc0OSAzNS41MTY3VjQ4LjA0OTlaIiBmaWxsPSJ3aGl0ZSIvPgo8cGF0aCBkPSJNMTc4LjAwMSAzNS41MTY3QzE3OC4wMDEgMzUuNDgyNiAxNzguMDE0IDM1LjQ1NyAxNzguMDM1IDM1LjQzMTVDMTc4LjA1NiAzNS40MDU5IDE3OC4wODIgMzUuNDAxNiAxNzguMTE2IDM1LjQwMTZIMTgwLjQ1QzE4MC40NzYgMzUuNDAxNiAxODAuNDk3IDM1LjQxNDQgMTgwLjUyMyAzNS40MzE1QzE4MC41NDQgMzUuNDUyOCAxODAuNTUzIDM1LjQ4MjYgMTgwLjU1MyAzNS41MTY3VjQ4LjA0OTlDMTgwLjU1MyA0OC4wNzU1IDE4MC41NCA0OC4wOTY4IDE4MC41MjMgNDguMTIyNEMxODAuNTAyIDQ4LjE0MzcgMTgwLjQ3NiA0OC4xNTIyIDE4MC40NSA0OC4xNTIySDE3NS42ODNDMTc0LjcwOCA0OC4xMjY2IDE3My45MzcgNDcuNzEzNCAxNzMuMzc0IDQ2LjkxNjhMMTY3LjQ0NCAzOC4wODEzQzE2Ny4xOTcgMzcuNzU3NSAxNjYuODkgMzcuNTk1NiAxNjYuNTI4IDM3LjU5NTZIMTY0Ljg5N1Y0OC4wNDk5QzE2NC44OTcgNDguMDc1NSAxNjQuODg0IDQ4LjA5NjggMTY0Ljg2NyA0OC4xMjI0QzE2NC44NDYgNDguMTQzNyAxNjQuODIgNDguMTUyMiAxNjQuNzgyIDQ4LjE1MjJIMTYyLjQ0N0MxNjIuNDIyIDQ4LjE1MjIgMTYyLjQgNDguMTM5NCAxNjIuMzc5IDQ4LjEyMjRDMTYyLjM1OCA0OC4xMDUzIDE2Mi4zNDUgNDguMDc1NSAxNjIuMzQ1IDQ4LjA0OTlWMzUuNTE2N0MxNjIuMzQ1IDM1LjQ4MjYgMTYyLjM1OCAzNS40NTcgMTYyLjM3OSAzNS40MzE1QzE2Mi40IDM1LjQwNTkgMTYyLjQyMiAzNS40MDE2IDE2Mi40NDcgMzUuNDAxNkgxNjcuMjE0QzE2OC4xOSAzNS40MzU3IDE2OC45NjEgMzUuODUzMiAxNjkuNTIzIDM2LjY0OTlMMTc1LjQ1MyA0NS40NzI2QzE3NS42OTIgNDUuODA0OSAxNzUuOTk5IDQ1Ljk3MSAxNzYuMzY5IDQ1Ljk3MUgxNzguMDAxVjM1LjUxNjdaIiBmaWxsPSJ3aGl0ZSIvPgo8cGF0aCBkPSJNMTg2LjcwNCA0OC4wNDk5QzE4Ni43MDQgNDguMDc1NSAxODYuNjkyIDQ4LjA5NjggMTg2LjY3NCA0OC4xMjI0QzE4Ni42NTMgNDguMTQzNyAxODYuNjI4IDQ4LjE1MjIgMTg2LjU4OSA0OC4xNTIySDE4NC4yNTVDMTg0LjIyOSA0OC4xNTIyIDE4NC4yMDggNDguMTM5NCAxODQuMTg3IDQ4LjEyMjRDMTg0LjE2NSA0OC4xMDUzIDE4NC4xNTIgNDguMDc1NSAxODQuMTUyIDQ4LjA0OTlWMzUuNTE2N0MxODQuMTUyIDM1LjQ4MjYgMTg0LjE2NSAzNS40NTcgMTg0LjE4NyAzNS40MzE1QzE4NC4yMDggMzUuNDA1OSAxODQuMjI5IDM1LjQwMTYgMTg0LjI1NSAzNS40MDE2SDE4Ni41ODlDMTg2LjYyMyAzNS40MDE2IDE4Ni42NDkgMzUuNDE0NCAxODYuNjc0IDM1LjQzMTVDMTg2LjY5NiAzNS40NTI4IDE4Ni43MDQgMzUuNDgyNiAxODYuNzA0IDM1LjUxNjdWNDguMDQ5OVoiIGZpbGw9IndoaXRlIi8+CjxwYXRoIGQ9Ik0xOTAuMTY4IDM1LjUxNjdDMTkwLjE2OCAzNS40ODI2IDE5MC4xNzYgMzUuNDU3IDE5MC4xOTggMzUuNDMxNUMxOTAuMjE5IDM1LjQxMDIgMTkwLjI0NCAzNS40MDE2IDE5MC4yNyAzNS40MDE2SDIwOC4yNzNDMjA4LjI5OSAzNS40MDE2IDIwOC4zMiAzNS40MTQ0IDIwOC4zNDEgMzUuNDMxNUMyMDguMzYzIDM1LjQ1MjggMjA4LjM3NiAzNS40ODI2IDIwOC4zNzYgMzUuNTE2N1YzNy40ODA2QzIwOC4zNzYgMzcuNTA2MSAyMDguMzYzIDM3LjUyNzQgMjA4LjM0MSAzNy41NDg3QzIwOC4zMiAzNy41NyAyMDguMjk5IDM3LjU4MjggMjA4LjI3MyAzNy41ODI4SDIwMC41NDVWNDguMDM3MkMyMDAuNTQ1IDQ4LjA3MTIgMjAwLjUzMyA0OC4wOTY4IDIwMC41MTEgNDguMTIyNEMyMDAuNDkgNDguMTQzNyAyMDAuNDY5IDQ4LjE1MjIgMjAwLjQ0MyA0OC4xNTIySDE5OC4xMDlDMTk4LjA3NSA0OC4xNTIyIDE5OC4wNDkgNDguMTM5NCAxOTguMDIzIDQ4LjEyMjRDMTk4LjAwMiA0OC4xMDExIDE5Ny45OTQgNDguMDc1NSAxOTcuOTk0IDQ4LjAzNzJWMzcuNTgyOEgxOTAuMjY2QzE5MC4yNCAzNy41ODI4IDE5MC4yMTkgMzcuNTcgMTkwLjE5MyAzNy41NDg3QzE5MC4xNzIgMzcuNTI3NCAxOTAuMTY0IDM3LjUwNjEgMTkwLjE2NCAzNy40ODA2VjM1LjUxNjdIMTkwLjE2OFoiIGZpbGw9IndoaXRlIi8+CjxwYXRoIGQ9Ik0yMTQuMzI3IDQwLjY3OTlIMjI4LjA1N0MyMjguMDkxIDQwLjY3OTkgMjI4LjExNyA0MC42OTI3IDIyOC4xMzggNDAuNzA5OEMyMjguMTYgNDAuNzMxMSAyMjguMTY4IDQwLjc2MDkgMjI4LjE2OCA0MC43OTVWNDIuNzU4OUMyMjguMTY4IDQyLjc5MyAyMjguMTYgNDIuODE4NSAyMjguMTM4IDQyLjg0NDFDMjI4LjExNyA0Mi44NjU0IDIyOC4wOTEgNDIuODczOSAyMjguMDU3IDQyLjg3MzlIMjE0LjMyN1Y0NC44NzYyQzIxNC4zMjcgNDUuMTc0NCAyMTQuNDMzIDQ1LjQzIDIxNC42NDYgNDUuNjQ3MkMyMTQuODU5IDQ1Ljg2NDUgMjE1LjExOSA0NS45NzEgMjE1LjQyNiA0NS45NzFIMjI5Ljg4NUMyMjkuOTE5IDQ1Ljk3MSAyMjkuOTQ5IDQ1Ljk4MzggMjI5Ljk2NiA0Ni4wMDUxQzIyOS45ODcgNDYuMDI2NCAyMzAgNDYuMDQ3NyAyMzAgNDYuMDczM1Y0OC4wMzcyQzIzMCA0OC4wNzEyIDIyOS45ODcgNDguMDk2OCAyMjkuOTY2IDQ4LjEyMjRDMjI5Ljk0NSA0OC4xNDM3IDIyOS45MTkgNDguMTUyMiAyMjkuODg1IDQ4LjE1MjJIMjE1LjQyNkMyMTQuNDE2IDQ4LjE1MjIgMjEzLjU1NiA0Ny43OTg2IDIxMi44NDQgNDcuMDg3MkMyMTIuMTMzIDQ2LjM3NTcgMjExLjc3OSA0NS41MTk0IDIxMS43NzkgNDQuNTA1NVYzOS4wNDgzQzIxMS43NzkgMzguMDM4NyAyMTIuMTMzIDM3LjE3ODEgMjEyLjg0NCAzNi40NjY3QzIxMy41NTYgMzUuNzU1MiAyMTQuNDE2IDM1LjQwMTYgMjE1LjQyNiAzNS40MDE2SDIyOS44ODVDMjI5LjkxOSAzNS40MDE2IDIyOS45NDkgMzUuNDE0NCAyMjkuOTY2IDM1LjQzMTVDMjI5Ljk4NyAzNS40NTI4IDIzMCAzNS40ODI2IDIzMCAzNS41MTY3VjM3LjQ4MDZDMjMwIDM3LjUwNjEgMjI5Ljk4NyAzNy41Mjc0IDIyOS45NjYgMzcuNTQ4N0MyMjkuOTQ1IDM3LjU3IDIyOS45MTkgMzcuNTgyOCAyMjkuODg1IDM3LjU4MjhIMjE1LjQyNkMyMTUuMTE5IDM3LjU4MjggMjE0Ljg1OSAzNy42ODkzIDIxNC42NDYgMzcuOTA2NkMyMTQuNDMzIDM4LjEyMzkgMjE0LjMyNyAzOC4zNzk1IDIxNC4zMjcgMzguNjc3N1Y0MC42Nzk5WiIgZmlsbD0id2hpdGUiLz4KPC9zdmc+"

def generate_html(status_msg):
    with data_lock:
        rs = race_state

    obs       = obstacle_present() if rs == STATE_IDLE else False
    top_light = "green"
    red_on    = False
    if obs or rs == STATE_OBSTACLE:
        top_light = "yellow"
    if rs in (STATE_IN_PROGRESS, STATE_START):
        top_light = ""
        red_on    = True
    red_cls    = "on" if red_on else ""
    red_lights = "".join(f'<div class="redlight {red_cls}"></div>' for _ in range(5))

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Race Pakistan – F1 Timing</title>
<style>
@font-face{{font-family:'Bison';src:url('/assets/bison.ttf') format('truetype');font-display:swap}}
@font-face{{font-family:'Overpass';src:url('/assets/overpass.woff2') format('woff2');font-weight:100 900;font-display:swap}}
*,*::before,*::after{{box-sizing:border-box;margin:0;padding:0}}
:root{{--orange:#ff5635;--orange-soft:#ff8c42;--ember:#f5451d;--amber:#ffbb00;--plum:#561744;--violet:#25083b;--crimson:#900c3e;--magenta:#83006b;--green:#22c55e;--bg:#13050f;--bg-2:#1a0a14;--line:rgba(255,86,53,.16);--line-soft:rgba(247,239,244,.08);--text:#f7eff4;--muted:#c3a9ba;--dim:#6e5566;--display:'Bison','Arial Narrow',sans-serif;--body:'Overpass',system-ui,sans-serif}}
body{{background:radial-gradient(1100px 620px at 50% -12%,rgba(86,23,68,.6),transparent 68%),linear-gradient(180deg,var(--bg) 0%,var(--bg-2) 100%);background-attachment:fixed;color:var(--text);font-family:var(--body);min-height:100vh;overflow-x:hidden;-webkit-font-smoothing:antialiased}}
body::after{{content:'';position:fixed;inset:0;background:repeating-linear-gradient(0deg,transparent,transparent 2px,rgba(0,0,0,.05) 2px,rgba(0,0,0,.05) 4px);pointer-events:none;z-index:9999}}
header{{display:flex;justify-content:space-between;align-items:center;padding:16px 5vw;position:relative;background:linear-gradient(180deg,#2a0b22 0%,rgba(19,5,15,0) 100%)}}
header::after{{content:'';position:absolute;left:0;right:0;bottom:0;height:3px;background:linear-gradient(90deg,var(--crimson),var(--orange),var(--amber),var(--orange),var(--crimson))}}
.logo-rp{{display:flex;align-items:center;justify-content:flex-start;flex:1 1 0;max-width:220px}}
.logo-rp img{{height:40px;width:auto;object-fit:contain}}
header h1{{font-family:var(--display);font-size:clamp(18px,3vw,34px);letter-spacing:2px;text-align:center;line-height:1.4;color:var(--text);flex:0 0 auto}}
.logo-nuvex{{display:flex;align-items:center;justify-content:flex-end;flex:1 1 0;max-width:220px}}
.logo-nuvex img{{height:34px;width:auto;max-width:100%;object-fit:contain}}
.lights-wrap{{display:flex;flex-direction:column;align-items:center;gap:14px;padding:30px 0 18px}}
.top-light{{width:50px;height:50px;border-radius:50%;background:radial-gradient(circle at 34% 28%,#2a1420,#120309);border:2px solid rgba(247,239,244,.07);box-shadow:inset 0 2px 7px rgba(0,0,0,.65);transition:background .3s,box-shadow .3s,border-color .3s}}
.top-light.green{{background:radial-gradient(circle at 34% 28%,#8bf7bb,var(--green));border-color:rgba(34,197,94,.45);box-shadow:0 0 28px var(--green),0 0 62px rgba(34,197,94,.4),inset 0 2px 8px rgba(0,0,0,.22)}}
.top-light.yellow{{background:radial-gradient(circle at 34% 28%,#ffe27a,var(--amber));border-color:rgba(255,187,0,.45);box-shadow:0 0 28px var(--amber),0 0 62px rgba(255,187,0,.4),inset 0 2px 8px rgba(0,0,0,.22)}}
.red-row{{display:flex;gap:14px}}
.redlight{{width:42px;height:42px;border-radius:50%;background:radial-gradient(circle at 34% 28%,#2c0a14,#14040c);border:2px solid rgba(247,239,244,.06);box-shadow:inset 0 2px 6px rgba(0,0,0,.65);transition:background .3s,box-shadow .3s,border-color .3s}}
.redlight.on{{background:radial-gradient(circle at 34% 28%,#ff6b4d 0%,var(--ember) 45%,var(--crimson) 100%);border-color:rgba(245,69,29,.55);box-shadow:0 0 26px rgba(245,69,29,.9),0 0 56px rgba(144,12,62,.55),inset 0 2px 8px rgba(0,0,0,.28)}}
.status-bar{{text-align:center;padding:14px 20px;font-family:var(--display);font-size:clamp(13px,2vw,20px);letter-spacing:3px;color:var(--amber);background:linear-gradient(90deg,transparent,rgba(86,23,68,.5),transparent);border-top:1px solid var(--line);border-bottom:1px solid var(--line);margin-bottom:28px;text-transform:uppercase}}
.cards{{display:grid;grid-template-columns:1fr 1fr;gap:3vw;padding:0 5vw 36px;max-width:1200px;margin:0 auto}}
@media(max-width:680px){{.cards{{grid-template-columns:1fr}}header{{flex-direction:column;gap:10px;text-align:center}}.summary-section{{flex-direction:column}}}}
.card{{background:linear-gradient(145deg,rgba(86,23,68,.4) 0%,rgba(19,5,15,.92) 100%);border:1px solid var(--line);border-top:5px solid var(--orange);border-radius:18px;padding:32px 36px 28px;position:relative;overflow:hidden;box-shadow:0 18px 44px rgba(0,0,0,.45)}}
.card::before{{content:'';position:absolute;top:0;left:0;right:0;height:1px;background:linear-gradient(90deg,transparent,rgba(255,187,0,.55),transparent)}}
.ghost{{font-family:var(--display);font-size:clamp(58px,9vw,104px);color:rgba(255,86,53,.05);position:absolute;right:16px;top:4px;line-height:1;user-select:none;pointer-events:none}}
.card h2{{font-family:var(--display);font-size:clamp(16px,2.1vw,24px);letter-spacing:3px;color:var(--orange);margin-bottom:22px;text-transform:uppercase}}
.stat{{display:flex;justify-content:space-between;align-items:center;padding:13px 0;border-bottom:1px solid var(--line-soft)}}
.stat:last-child{{border-bottom:none}}
.lbl{{font-family:var(--body);font-size:clamp(11px,1.3vw,14px);font-weight:600;letter-spacing:2px;color:var(--muted);text-transform:uppercase}}
.val{{font-family:var(--display);font-size:clamp(22px,2.9vw,34px);letter-spacing:1px;color:var(--amber);font-variant-numeric:tabular-nums;line-height:1}}
.val.dim{{color:#4a3342;font-size:clamp(16px,2vw,24px)}}
.unit{{font-family:var(--body);font-size:.42em;font-weight:600;letter-spacing:1px;color:var(--dim);text-transform:uppercase}}
.summary-section{{display:flex;gap:3vw;padding:0 5vw 20px;max-width:1200px;margin:0 auto;flex-wrap:wrap}}
.summary-card{{flex:1;min-width:280px;background:linear-gradient(150deg,rgba(37,8,59,.55) 0%,rgba(19,5,15,.92) 100%);border:1px solid var(--line-soft);border-top:4px solid var(--magenta);border-radius:14px;padding:24px 28px 20px;box-shadow:0 14px 34px rgba(0,0,0,.35)}}
.sum-title{{font-family:var(--display);font-size:clamp(13px,1.7vw,19px);letter-spacing:3px;color:var(--orange-soft);margin-bottom:18px;text-transform:uppercase}}
.sum-boxes{{display:flex;gap:14px;margin-bottom:20px}}
.sumbox{{flex:1;background:rgba(37,8,59,.45);border:1px solid var(--line-soft);border-radius:10px;padding:12px 14px;text-align:center}}
.sblbl{{font-family:var(--body);font-size:10px;font-weight:600;letter-spacing:2px;color:var(--dim);text-transform:uppercase;margin-bottom:6px}}
.sbval{{font-family:var(--display);font-size:clamp(19px,2.3vw,27px);letter-spacing:1px;color:var(--amber);font-variant-numeric:tabular-nums}}
.sum-tables{{display:flex;gap:14px}}
.sum-col{{flex:1}}
.sum-col-title{{font-family:var(--body);font-size:10px;font-weight:600;letter-spacing:2px;color:var(--dim);text-transform:uppercase;margin-bottom:8px;text-align:center}}
.att-grid{{display:grid;grid-template-columns:1fr 1fr;gap:5px}}
.att-cell{{background:rgba(247,239,244,.035);border:1px solid var(--line-soft);border-radius:6px;padding:7px 9px;font-size:clamp(10px,1vw,12px);letter-spacing:0;white-space:nowrap;color:var(--muted);font-variant-numeric:tabular-nums}}
.att-cell b{{color:var(--amber);font-weight:700}}
.att-empty{{color:var(--dim);grid-column:1/-1;text-align:center}}
.top-list{{display:flex;flex-direction:column;gap:5px}}
.top-cell{{background:rgba(247,239,244,.035);border:1px solid var(--line-soft);border-radius:6px;padding:8px 11px;font-size:clamp(10px,1vw,12px);letter-spacing:0;white-space:nowrap;color:var(--muted);font-variant-numeric:tabular-nums}}
.top-cell b{{color:var(--amber);font-weight:700}}
.dl-wrap{{text-align:center;padding:22px 0 36px}}
.dl-btn{{font-family:var(--display);font-size:clamp(14px,1.5vw,17px);letter-spacing:2px;color:#1a0a14;background:var(--orange);border:none;border-radius:999px;padding:13px 36px;cursor:pointer;text-transform:uppercase;box-shadow:0 10px 26px rgba(255,86,53,.28);transition:background .2s,transform .2s,box-shadow .2s}}
.dl-btn:hover{{background:var(--ember);transform:translateY(-2px);box-shadow:0 14px 30px rgba(245,69,29,.36)}}
.dl-btn:active{{transform:translateY(0)}}
.dl-btn:focus-visible{{outline:2px solid var(--amber);outline-offset:3px}}
footer{{text-align:center;padding:22px;color:var(--dim);font-family:var(--body);font-size:12px;font-weight:600;letter-spacing:2px;border-top:1px solid var(--line-soft);text-transform:uppercase}}
</style>
</head>
<body>
<header>
  <div class="logo-rp"><img src="{_RP_LOGO}" alt="Race Pakistan"></div>
  <h1>TIMING SYSTEM</h1>
  <div class="logo-nuvex"><img src="{_NUVEX_LOGO}" alt="NUVEX — Think Infinite"></div>
</header>
<div class="lights-wrap">
  <div class="top-light {top_light}" id="top-light"></div>
  <div class="red-row">{red_lights}</div>
</div>
<div class="status-bar" id="status-bar">{status_msg}</div>
<div class="cards">
  <div class="card">
    <div class="ghost">1</div>
    <h2>Player 1</h2>
    <div class="stat"><span class="lbl">Reaction Time</span><span id="p1-reaction" class="val dim">--</span></div>
    <div class="stat"><span class="lbl">Race Time</span><span id="p1-race" class="val dim">--</span></div>
    <div class="stat"><span class="lbl">Total Time</span><span id="p1-total" class="val dim">--</span></div>
  </div>
  <div class="card">
    <div class="ghost">2</div>
    <h2>Player 2</h2>
    <div class="stat"><span class="lbl">Reaction Time</span><span id="p2-reaction" class="val dim">--</span></div>
    <div class="stat"><span class="lbl">Race Time</span><span id="p2-race" class="val dim">--</span></div>
    <div class="stat"><span class="lbl">Total Time</span><span id="p2-total" class="val dim">--</span></div>
  </div>
</div>
<div class="summary-section">
  <div class="summary-card">
    <div class="sum-title">ATTEMPT SUMMARY — PLAYER 1</div>
    <div class="sum-boxes">
      <div class="sumbox"><div class="sblbl">BEST TIME</div><div class="sbval" id="p1-best">--</div></div>
      <div class="sumbox"><div class="sblbl">AVG OF BEST 4</div><div class="sbval" id="p1-avg4">--</div></div>
    </div>
    <div class="sum-tables">
      <div class="sum-col"><div class="sum-col-title">ALL ATTEMPTS</div><div class="att-grid" id="p1-all"><div class="att-cell att-empty" style="grid-column:1/-1">No attempts yet</div></div></div>
      <div class="sum-col"><div class="sum-col-title">TOP 4 BEST ATTEMPTS</div><div class="top-list" id="p1-top4"><div class="top-cell att-empty">--</div></div></div>
    </div>
  </div>
  <div class="summary-card">
    <div class="sum-title">ATTEMPT SUMMARY — PLAYER 2</div>
    <div class="sum-boxes">
      <div class="sumbox"><div class="sblbl">BEST TIME</div><div class="sbval" id="p2-best">--</div></div>
      <div class="sumbox"><div class="sblbl">AVG OF BEST 4</div><div class="sbval" id="p2-avg4">--</div></div>
    </div>
    <div class="sum-tables">
      <div class="sum-col"><div class="sum-col-title">ALL ATTEMPTS</div><div class="att-grid" id="p2-all"><div class="att-cell att-empty" style="grid-column:1/-1">No attempts yet</div></div></div>
      <div class="sum-col"><div class="sum-col-title">TOP 4 BEST ATTEMPTS</div><div class="top-list" id="p2-top4"><div class="top-cell att-empty">--</div></div></div>
    </div>
  </div>
</div>
<div class="dl-wrap"><button class="dl-btn" onclick="downloadCSV()">&#x25BC; Download Attempts</button></div>
<footer>© 2025 · NUVEX × Race Pakistan · All Rights Reserved</footer>
<script>
function fmt(v) {{
  if (v===null||v===undefined) return '<span class="val dim">--</span>';
  if (v===0) return '<span class="val dim">EMPTY</span>';
  return `<span class="val">${{v}} <span class="unit">ms</span></span>`;
}}
function fmtMs(v) {{ return (v!==null&&v!==undefined) ? v+\' ms\' : \'--\'; }}
function updateLights(state) {{
  const top=document.getElementById('top-light');
  const reds=document.querySelectorAll('.redlight');
  top.className='top-light';
  reds.forEach(r=>r.classList.remove('on'));
  if(state==='OBSTACLE') top.classList.add('yellow');
  else if(state==='IDLE'||state==='COMPLETE') top.classList.add('green');
  else if(state==='IN_PROGRESS'||state==='START') reds.forEach(r=>r.classList.add('on'));
}}
function updateSummary(p,s) {{
  document.getElementById(p+'-best').textContent=fmtMs(s.best);
  document.getElementById(p+'-avg4').textContent=fmtMs(s.avg4);
  const grid=document.getElementById(p+'-all');
  grid.innerHTML=s.all.length?s.all.map((t,i)=>`<div class="att-cell">Attempt ${{i+1}}: <b>${{fmtMs(t)}}</b></div>`).join(''):'<div class="att-cell att-empty" style="grid-column:1/-1">No attempts yet</div>';
  const top4=document.getElementById(p+'-top4');
  top4.innerHTML=s.top4.length?s.top4.map((t,i)=>`<div class="top-cell">Top ${{i+1}}: <b>${{fmtMs(t)}}</b></div>`).join(''):'<div class="top-cell att-empty">--</div>';
}}
function poll() {{
  fetch('/data').then(r=>r.json()).then(d=>{{
    document.getElementById('status-bar').textContent=d.status;
    updateLights(d.state);
    document.getElementById('p1-reaction').innerHTML=fmt(d.p1.reaction);
    document.getElementById('p1-race').innerHTML=fmt(d.p1.race);
    document.getElementById('p1-total').innerHTML=fmt(d.p1.total);
    document.getElementById('p2-reaction').innerHTML=fmt(d.p2.reaction);
    document.getElementById('p2-race').innerHTML=fmt(d.p2.race);
    document.getElementById('p2-total').innerHTML=fmt(d.p2.total);
    updateSummary('p1',d.p1_stats);
    updateSummary('p2',d.p2_stats);
  }}).catch(()=>{{}}).finally(()=>setTimeout(poll,1000));
}}
poll();
function downloadCSV() {{
  fetch('/data').then(r=>r.json()).then(d=>{{
    let csv='Attempt,P1 Reaction (ms),P1 Race (ms),P1 Total (ms),P2 Reaction (ms),P2 Race (ms),P2 Total (ms)\\n';
    const sv=v=>v!==null&&v!==undefined?v:'';
    d.attempts.forEach(e=>{{csv+=`${{e.attempt}},${{sv(e.p1_reaction)}},${{sv(e.p1_race)}},${{sv(e.p1_total)}},${{sv(e.p2_reaction)}},${{sv(e.p2_race)}},${{sv(e.p2_total)}}\
`;}});
    const b=new Blob([csv],{{type:'text/csv'}});
    const u=URL.createObjectURL(b);
    const a=document.createElement('a');
    a.href=u;a.download='race_pakistan_attempts.csv';a.click();
    URL.revokeObjectURL(u);
  }});
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

    def _status_msg(self, rs):
        if rs == STATE_OBSTACLE:            return "OBSTACLE DETECTED — CLEAR THE TRACK"
        elif rs == STATE_IN_PROGRESS:       return "RACE IN PROGRESS"
        elif rs == STATE_COMPLETE:          return "RACE COMPLETE"
        elif rs == STATE_START:             return "GET READY..."
        elif obstacle_present():            return "OBSTACLE DETECTED — CLEAR THE TRACK"
        else:                               return "TRACK CLEAR — READY"

    def do_GET(self):
        if self.path == "/data":
            self._serve_json()
        elif self.path.startswith("/assets/"):
            self._serve_asset()
        else:
            self._serve_html()

    def _serve_asset(self):
        """Serve font files from the assets/ folder next to this script."""
        import os
        name = os.path.basename(self.path)          # no directory traversal
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", name)
        ctype = {".woff2": "font/woff2",
                 ".ttf":   "font/ttf",
                 ".otf":   "font/otf"}.get(os.path.splitext(name)[1].lower())
        if not ctype or not os.path.isfile(path):
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        body = open(path, "rb").read()
        self.send_response(200)
        self.send_header("Content-Type",   ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control",  "public, max-age=31536000")
        self.end_headers()
        self.wfile.write(body)

    def _serve_json(self):
        import json
        with data_lock:
            rs   = race_state
            r1   = dict(p1)
            r2   = dict(p2)
            hist = list(attempt_history)

        def stats(player):
            totals = [e[f"{player}_total"] for e in hist if e[f"{player}_total"] is not None]
            if not totals:
                return None, None, [], []
            st = sorted(totals)
            top4 = st[:4]
            return st[0], round(sum(top4)/len(top4)), totals, top4

        p1b, p1a, p1all, p1t4 = stats("p1")
        p2b, p2a, p2all, p2t4 = stats("p2")

        data = {
            "state":    rs,
            "status":   self._status_msg(rs),
            "p1":       r1,
            "p2":       r2,
            "p1_stats": {"best": p1b, "avg4": p1a, "all": p1all, "top4": p1t4},
            "p2_stats": {"best": p2b, "avg4": p2a, "all": p2all, "top4": p2t4},
            "attempts": hist,
        }
        body = json.dumps(data).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type",   "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control",  "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def _serve_html(self):
        with data_lock:
            rs = race_state
        html = generate_html(self._status_msg(rs)).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type",   "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(html)))
        self.end_headers()
        self.wfile.write(html)

def start_web_server():
    class QuietServer(HTTPServer):
        def handle_error(self, request, client_address):
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

    calibrate_sensors()

    threading.Thread(target=start_web_server,    daemon=True).start()
    threading.Thread(target=obstacle_led_thread, daemon=True).start()

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

    GPIO.output(STATUS_LED, LED_OFF)  # OFF = idle/ready
    print("✅  System ready!\n")
    print("    Press the MASTER BUTTON to start a race.\n")

    try:
        while True:

            # ── IDLE: status OFF (obstacle LEDs handled by background thread) ──
            GPIO.output(STATUS_LED, LED_OFF)

            if obstacle_present():
                with data_lock:
                    race_state = STATE_OBSTACLE
                print("🚧  Obstacle detected — waiting for track to clear...")
                while obstacle_present():
                    time.sleep(0.1)
                with data_lock:
                    race_state = STATE_IDLE
                print("✅  Track clear!")

            # ── Wait for master button ─────────────────────────────
            while GPIO.input(MASTER_BUTTON) == GPIO.HIGH:
                time.sleep(0.05)
            while GPIO.input(MASTER_BUTTON) == GPIO.LOW:   # wait for release
                time.sleep(0.01)

            if obstacle_present():
                print("⚠️   Button pressed but obstacle detected! Clear track first.")
                with data_lock:
                    race_state = STATE_OBSTACLE
                while obstacle_present():
                    time.sleep(0.1)
                with data_lock:
                    race_state = STATE_IDLE
                print("✅  Track clear — press button again to start.")
                continue

            # ── Track clear + button pressed → START ──────────────
            print("━" * 46)
            print("🟢  MASTER button pressed — race sequence starting!")

            GPIO.output(STATUS_LED, LED_ON)   # ON = race active

            with data_lock:
                race_state      = STATE_START
                lights_off_time = None
                reset_player(p1)
                reset_player(p2)

            # ── Lights on one by one ───────────────────────────────
            print("🔴  Lights turning on...")
            for i, pin in enumerate(LED_PINS, 1):
                GPIO.output(pin, LED_ON)
                print(f"    Light {i} ON")
                time.sleep(0.5)

            # ── Hold ON for random 1–3 seconds ─────────────────────
            hold_sec = random.uniform(1.0, 3.0)
            print(f"⏳  Lights holding for {hold_sec:.2f}s...")
            time.sleep(hold_sec)

            # ── ALL lights OFF → reaction clock starts ──────────────
            for pin in LED_PINS:
                GPIO.output(pin, LED_OFF)

            with data_lock:
                lights_off_time = ms()
                race_state      = STATE_IN_PROGRESS

            print("🏁  LIGHTS OUT — GO GO GO!")

            # ── Wait for both players to finish (max 25 sec) ────────
            deadline = ms() + 25_000
            while ms() < deadline:
                with data_lock:
                    done1 = p1["total"] is not None
                    done2 = p2["total"] is not None
                if done1 and done2:
                    break
                time.sleep(0.1)

            # ── Race complete ────────────────────────────────────────
            with data_lock:
                race_state = STATE_COMPLETE

            record_attempt()

            print("\n✅  RACE COMPLETE")
            with data_lock:
                print(f"   P1 → Reaction: {p1['reaction']} ms | Race: {p1['race']} ms | Total: {p1['total']} ms")
                print(f"   P2 → Reaction: {p2['reaction']} ms | Race: {p2['race']} ms | Total: {p2['total']} ms")

            time.sleep(5)

            GPIO.output(STATUS_LED, LED_OFF)  # back to idle
            with data_lock:
                race_state = STATE_IDLE

            print("\n🔄  Ready for next race.")
            print("    Press MASTER BUTTON to start.\n")

    except KeyboardInterrupt:
        print("\n🛑  Shutting down...")
    finally:
        for pin in LED_PINS:
            GPIO.output(pin, LED_OFF)
        GPIO.output(STATUS_LED,      LED_OFF)
        GPIO.output(TRACK1_OBST_LED, LED_OFF)
        GPIO.output(TRACK2_OBST_LED, LED_OFF)
        GPIO.cleanup()
        print("✅  GPIO cleaned up. Goodbye!")


if __name__ == "__main__":
    main()
