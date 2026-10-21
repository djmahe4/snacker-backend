#!/usr/bin/env python3
"""
=============================================================================
 SNACKER HARDWARE VENDING CONTROLLER (Raspberry Pi 3 / 4 / 5)
 - USB QR Scanner: Fingers QuickScan-Omni-W106 (USB HID via evdev)
 - Microcontroller: Arduino Mega 2560 (USB Serial @ 115200 baud)
 - Database: Google Cloud Firestore (Firebase Admin SDK)
 - Features:
     * Full Nanoid decode (A-Z, a-z, 0-9, _, -) with Shift handling
     * Order lock to 'dispensing' (anti-replay prevention)
     * Hardware IR beam break drop verification
     * Automated wallet credit refund on mechanical jam / empty slot
     * Error & incident audit logging in Firestore
     * USB hotplug auto-reconnect resilience
=============================================================================
"""

import os
import sys
import time
import serial
import serial.tools.list_ports
import evdev
from evdev import ecodes
import firebase_admin
from firebase_admin import credentials, firestore

# --- CONFIGURATION ---
SERIAL_BAUD = 115200
FIREBASE_KEY_PATH = os.environ.get(
    "FIREBASE_SERVICE_ACCOUNT_PATH",
    "/home/pi/snacker/serviceAccountKey.json"
)

# Linux evdev scancode table to decode Nanoid order IDs (A-Z, a-z, 0-9, _, -)
SCANCODE_MAP = {
    ecodes.KEY_1: ("1", "!"), ecodes.KEY_2: ("2", "@"), ecodes.KEY_3: ("3", "#"),
    ecodes.KEY_4: ("4", "$"), ecodes.KEY_5: ("5", "%"), ecodes.KEY_6: ("6", "^"),
    ecodes.KEY_7: ("7", "&"), ecodes.KEY_8: ("8", "*"), ecodes.KEY_9: ("9", "("),
    ecodes.KEY_0: ("0", ")"),
    ecodes.KEY_A: ("a", "A"), ecodes.KEY_B: ("b", "B"), ecodes.KEY_C: ("c", "C"),
    ecodes.KEY_D: ("d", "D"), ecodes.KEY_E: ("e", "E"), ecodes.KEY_F: ("f", "F"),
    ecodes.KEY_G: ("g", "G"), ecodes.KEY_H: ("h", "H"), ecodes.KEY_I: ("i", "I"),
    ecodes.KEY_J: ("j", "J"), ecodes.KEY_K: ("k", "K"), ecodes.KEY_L: ("l", "L"),
    ecodes.KEY_M: ("m", "M"), ecodes.KEY_N: ("n", "N"), ecodes.KEY_O: ("o", "O"),
    ecodes.KEY_P: ("p", "P"), ecodes.KEY_Q: ("q", "Q"), ecodes.KEY_R: ("r", "R"),
    ecodes.KEY_S: ("s", "S"), ecodes.KEY_T: ("t", "T"), ecodes.KEY_U: ("u", "U"),
    ecodes.KEY_V: ("v", "V"), ecodes.KEY_W: ("w", "W"), ecodes.KEY_X: ("x", "X"),
    ecodes.KEY_Y: ("y", "Y"), ecodes.KEY_Z: ("z", "Z"),
    ecodes.KEY_MINUS: ("-", "_"),   # Handles both hyphen and underscore in nanoids
    ecodes.KEY_SLASH: ("/", "?"),
    ecodes.KEY_EQUAL: ("=", "+"),
}

# --- FIREBASE INITIALIZATION ---
def init_firebase():
    if not firebase_admin._apps:
        if os.path.exists(FIREBASE_KEY_PATH):
            cred = credentials.Certificate(FIREBASE_KEY_PATH)
            firebase_admin.initialize_app(cred)
            print(f"[FIREBASE] Initialized with credentials from {FIREBASE_KEY_PATH}")
        else:
            # Fallback to default application credentials if running in GCP environment
            firebase_admin.initialize_app()
            print("[FIREBASE] Initialized with application default credentials")
    return firestore.client()

db = init_firebase()

def find_arduino_port():
    """Detects Arduino Mega on USB serial ports."""
    ports = serial.tools.list_ports.comports()
    for p in ports:
        desc = (p.description or "").lower()
        dev = (p.device or "").lower()
        if "arduino" in desc or "ch340" in desc or "acm" in dev or "usb" in dev:
            return p.device
    return "/dev/ttyACM0"

def find_scanner_device():
    """Finds Fingers QuickScan-Omni-W106 USB HID device."""
    try:
        devices = [evdev.InputDevice(path) for path in evdev.list_devices()]
        for dev in devices:
            name = dev.name.lower()
            if any(k in name for k in ["scanner", "barcode", "omni", "fingers", "w106", "hid"]):
                return dev
        # Fallback to the first available keyboard device
        for dev in devices:
            if "keyboard" in dev.name.lower():
                return dev
    except Exception as e:
        print(f"[SCANNER] Device enumeration warning: {e}")
    return None

def send_arduino_dispense(ser, slot, count=1, timeout=12):
    """
    Sends dispense command to Arduino and monitors IR drop sensor telemetry:
      - 'DONE:<SLOT>:VERIFIED'        -> Item dropped successfully
      - 'WARN:<SLOT>:NO_DROP_DETECTED' -> Item jammed or coil empty
    """
    try:
        ser.reset_input_buffer()
        cmd = f"DISPENSE:{slot}:{count}\n"
        ser.write(cmd.encode("utf-8"))
        print(f"[ARDUINO ->] {cmd.strip()}")

        start = time.time()
        while time.time() - start < timeout:
            if ser.in_waiting > 0:
                line = ser.readline().decode("utf-8", errors="ignore").strip()
                print(f"[ARDUINO <-] {line}")
                if ":VERIFIED" in line:
                    return "SUCCESS"
                elif ":NO_DROP_DETECTED" in line:
                    return "JAMMED"
                elif line.startswith("ERROR:"):
                    return "ERROR"
            time.sleep(0.05)
        return "TIMEOUT"
    except Exception as e:
        print(f"[SERIAL] Communication error: {e}")
        return "ERROR"

def refund_credits(user_id, amount, order_id, reason):
    """Refunds credits back to student's wallet if a slot jams."""
    try:
        user_ref = db.collection("users").document(user_id)
        user_doc = user_ref.get()
        if user_doc.exists:
            current_credit = user_doc.to_dict().get("credit", 0)
            user_ref.update({"credit": current_credit + amount})
            print(f"[REFUND] Refunded {amount} pts to user '{user_id}' (Reason: {reason})")

        # Log incident in Firestore errors collection for inventory audit
        db.collection("errors").document(f"jam_{order_id}_{int(time.time())}").set({
            "orderId": order_id,
            "userId": user_id,
            "refundedAmount": amount,
            "reason": reason,
            "timestamp": firestore.SERVER_TIMESTAMP,
            "context": "hardware_dispenser",
        })
    except Exception as e:
        print(f"[ERROR] Refund transaction failed: {e}")

def process_order(order_id, ser):
    print("\n" + "=" * 60)
    print(f"[SCAN] Scanned Order ID: {order_id}")
    order_ref = db.collection("orders").document(order_id)
    doc_snap = order_ref.get()

    if not doc_snap.exists:
        print(f"[REJECT] Order '{order_id}' does not exist in database.")
        print("=" * 60 + "\n")
        return

    order_data = doc_snap.to_dict()
    status = order_data.get("status", "")
    user_id = order_data.get("userId", "")
    amount = order_data.get("amount", 0)
    products = order_data.get("products", [])

    if status != "pending":
        print(f"[REJECT] Order '{order_id}' has status '{status}'. Cannot vend.")
        print("=" * 60 + "\n")
        return

    # 1. Lock order state immediately to prevent replay / double scan
    order_ref.update({
        "status": "dispensing",
        "dispensedAt": firestore.SERVER_TIMESTAMP
    })

    all_vended = True
    failure_reason = ""

    # 2. Iterate through ordered items
    for item in products:
        slot = item.get("position", "A1").upper()
        count = int(item.get("count", 1))
        name = item.get("name", "Chips")

        print(f"[DISPATCH] Vending {count}x '{name}' from Slot [{slot}]...")
        result = send_arduino_dispense(ser, slot, count)

        if result == "SUCCESS":
            print(f"[OK] Slot {slot} dispense physically confirmed by IR sensor.")
        else:
            all_vended = False
            failure_reason = f"Slot {slot} status: {result}"
            print(f"[ALERT] {failure_reason}")
            break

    # 3. Finalize order status & execute automatic refund if jammed
    if all_vended:
        order_ref.update({
            "status": "completed",
            "completedAt": firestore.SERVER_TIMESTAMP
        })
        print(f"[COMPLETE] Order '{order_id}' successfully finished!")
    else:
        order_ref.update({
            "status": "jammed",
            "error": failure_reason,
            "updatedAt": firestore.SERVER_TIMESTAMP
        })
        # Auto-refund points so customer never loses money
        if user_id and amount > 0:
            refund_credits(user_id, amount, order_id, failure_reason)

    print("=" * 60 + "\n")

def main():
    print("=======================================================")
    print("      SNACKER HARDWARE VENDING DAEMON STARTING         ")
    print("=======================================================")

    # 1. Connect to Arduino Mega over Serial
    ser = None
    while ser is None:
        port = find_arduino_port()
        try:
            ser = serial.Serial(port, SERIAL_BAUD, timeout=2)
            time.sleep(2)  # Wait for Arduino bootloader reset
            ser.write(b"STATUS\n")
            print(f"[SERIAL] Connected to Arduino Mega on {port}")
        except Exception as e:
            print(f"[WAIT] Waiting for Arduino Mega on {port}... ({e})")
            time.sleep(3)

    # 2. Scanner Loop with Hotplug Resilience
    while True:
        scanner = find_scanner_device()
        if not scanner:
            print("[WAIT] Waiting for Fingers QuickScan scanner to be connected...")
            time.sleep(2)
            continue

        try:
            # Exclusively grab device so raw keystrokes don't leak into terminal
            scanner.grab()
            print(f"[READY] Listening for QR codes on: {scanner.name} ({scanner.path})")

            scanned_chars = []
            is_shift = False

            for event in scanner.read_loop():
                if event.type == ecodes.EV_KEY:
                    data = evdev.categorize(event)

                    # Track Shift key states
                    if data.scancode in (ecodes.KEY_LEFTSHIFT, ecodes.KEY_RIGHTSHIFT):
                        is_shift = (data.keystate != data.key_up)
                        continue

                    # On Key Press Down
                    if data.keystate == data.key_down:
                        if data.scancode == ecodes.KEY_ENTER:
                            order_id = "".join(scanned_chars).strip()
                            scanned_chars = []
                            if len(order_id) >= 6:  # Valid nanoid length
                                process_order(order_id, ser)
                        else:
                            mapping = SCANCODE_MAP.get(data.scancode)
                            if mapping:
                                char = mapping[1] if is_shift else mapping[0]
                                scanned_chars.append(char)

        except (IOError, OSError) as e:
            print(f"[DISCONNECT] Scanner unplugged or connection reset: {e}")
            time.sleep(2)
        finally:
            try:
                scanner.ungrab()
            except Exception:
                pass

if __name__ == "__main__":
    main()
