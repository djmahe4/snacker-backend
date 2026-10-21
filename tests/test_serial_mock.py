#!/usr/bin/env python3
"""
Simulated Arduino Mega Serial Responder
Use for testing vending_controller.py on desktop/laptop machines without physical hardware.
Emulates:
  - STATUS
  - PING -> PONG
  - TEST_ALL
  - DISPENSE:<SLOT>:<QTY> -> STATUS:DISPENSING:<SLOT> -> DONE:<SLOT>:VERIFIED
"""

import sys
import time

def handle_command(cmd):
    cmd = cmd.strip()
    if not cmd:
        return

    print(f"[MOCK ARDUINO RECV]: {cmd}")

    if cmd == "PING":
        print("PONG")
        return

    if cmd == "STATUS":
        print("INFO:SLOTS=A1,A2,A3,B1,B2,B3;DROP_SENSOR=CLEAR")
        return

    if cmd == "TEST_ALL":
        print("INFO:STARTING_SELF_TEST")
        for s in ["A1", "A2", "A3", "B1", "B2", "B3"]:
            print(f"TESTING:{s}")
            time.sleep(0.3)
        print("INFO:SELF_TEST_COMPLETE")
        return

    if cmd.startswith("DISPENSE:"):
        parts = cmd.split(":")
        if len(parts) >= 3:
            slot = parts[1]
            qty = int(parts[2]) if parts[2].isdigit() else 1
            for q in range(qty):
                print(f"STATUS:DISPENSING:{slot}")
                time.sleep(1.0)  # Simulate motor turning
                print(f"DONE:{slot}:VERIFIED")
                if q < qty - 1:
                    time.sleep(0.5)
            return

    print("ERROR:INVALID_SYNTAX")

def main():
    print("==================================================")
    print("   SNACKER MOCK ARDUINO MEGA SERIAL SIMULATOR     ")
    print("   Type Arduino commands (e.g. STATUS, DISPENSE:A1:1)")
    print("==================================================")

    while True:
        try:
            line = input("> ")
            handle_command(line)
        except (EOFError, KeyboardInterrupt):
            print("\nExiting simulator.")
            break

if __name__ == "__main__":
    main()
