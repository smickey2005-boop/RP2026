                update_obstacle_leds()
                while obstacle_present():
                    update_obstacle_leds()
                    time.sleep(0.1)
                update_obstacle_leds()
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

            # Obstacle LEDs off for duration of race
            GPIO.output(TRACK1_OBST_LED, LED_OFF)
            GPIO.output(TRACK2_OBST_LED, LED_OFF)

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
        GPIO.output(STATUS_LED, LED_OFF)
        GPIO.output(HOTSPOT_LED, LED_OFF)
        GPIO.output(TRACK1_OBST_LED, LED_OFF)
        GPIO.output(TRACK2_OBST_LED, LED_OFF)
        GPIO.cleanup()
        print("✅  GPIO cleaned up. Goodbye!")


if __name__ == "__main__":
    main()










