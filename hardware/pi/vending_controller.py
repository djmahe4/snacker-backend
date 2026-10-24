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
MOTOR_RUN_MS = int(os.environ.get("MOTOR_RUN_MS", "4500"))  # worst-case per-bag run; keep >= firmware value
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
        devices = []
        for path in evdev.list_devices():
            d = evdev.InputDevice(path)
            keys = d.capabilities().get(ecodes.EV_KEY, [])
            if ecodes.KEY_ENTER in keys and ecodes.KEY_A in keys:  # skips mice / consumer-control nodes
                devices.append(d)
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

def send_arduino_dispense(ser, slot, count=1, run_ms=MOTOR_RUN_MS):
    """
    Sends DISPENSE:<slot>:<count> and waits for one result line per bag:
      - 'DONE:<SLOT>:TIMED' / 'DONE:<SLOT>:VERIFIED' -> bag dispensed
      - 'WARN:<SLOT>:...'                            -> bag failed (jam / abort / skipped)
    Returns (status, delivered): status is SUCCESS only if every bag succeeded, otherwise
    JAMMED / TIMEOUT / ERROR; delivered is how many bags were confirmed before it stopped.
    """
    delivered = 0
    try:
        ser.reset_input_buffer()
        cmd = f"DISPENSE:{slot}:{count}\n"
        ser.write(cmd.encode("utf-8"))
        print(f"[ARDUINO ->] {cmd.strip()}")

        # Each bag: motor run + 1.2s cooldown, plus a safety margin
        timeout = count * (run_ms / 1000.0 + 1.5) + 5
        start = time.time()
        while time.time() - start < timeout:
            if ser.in_waiting > 0:
                line = ser.readline().decode("utf-8", errors="ignore").strip()
                if not line:
                    continue
                print(f"[ARDUINO <-] {line}")
                if line.startswith("ERROR:"):
                    return "ERROR", delivered
                if line.startswith("WARN:"):
                    return "JAMMED", delivered
                if line.startswith("DONE:"):
                    delivered += 1
                    if delivered >= count:
                        return "SUCCESS", delivered
            else:
                time.sleep(0.05)
        return "TIMEOUT", delivered
    except Exception as e:
        print(f"[SERIAL] Communication error: {e}")
        return "ERROR", delivered

class ArduinoLink:
    """Owns the serial connection and reopens it if the cable drops or the board resets."""

    def __init__(self):
        self.ser = None

    def _open(self):
        port = find_arduino_port()
        ser = serial.Serial(port, SERIAL_BAUD, timeout=2)
        time.sleep(2)  # Wait for Arduino bootloader reset
        ser.reset_input_buffer()
        ser.write(b"STATUS\n")
        print(f"[SERIAL] Connected to Arduino Mega on {port}")
        return ser

    def _alive(self):
        if self.ser is None:
            return False
        try:
            _ = self.ser.in_waiting  # raises OSError/SerialException once the device is gone
            return self.ser.is_open
        except Exception:
            return False

    def get(self):
        """Returns a live serial port, reconnecting once if needed; None if unavailable."""
        if self._alive():
            return self.ser
        self.drop()
        try:
            self.ser = self._open()
        except Exception as e:
            print(f"[SERIAL] Arduino unavailable: {e}")
            self.ser = None
        return self.ser

    def drop(self):
        if self.ser is not None:
            try:
                self.ser.close()
            except Exception:
                pass
        self.ser = None

def refund_credits(user_id, amount, order_id, reason, ambiguous=False):
    """Refunds credits back to student's wallet if a slot jams."""
    try:
        user_ref = db.collection("users").document(user_id)
        if user_ref.get().exists:
            user_ref.update({"credit": firestore.Increment(amount)})  # atomic, no lost updates
            print(f"[REFUND] Refunded {amount} pts to user '{user_id}' (Reason: {reason})")

        # Log incident in Firestore errors collection for inventory audit
        db.collection("errors").document(f"jam_{order_id}_{int(time.time())}").set({
            "orderId": order_id,
            "userId": user_id,
            "refundedAmount": amount,
            "reason": reason,
            "ambiguous": ambiguous,  # True when a bag may have dropped without a DONE (needs stock check)
            "timestamp": firestore.SERVER_TIMESTAMP,
            "context": "hardware_dispenser",
        })
    except Exception as e:
        print(f"[ERROR] Refund transaction failed: {e}")

def restore_stock(items):
    """Returns reserved stock for items that were never delivered."""
    for item in items:
        product_id = item.get("productId")
        if not product_id:
            continue
        try:
            db.collection("products").document(product_id).update(
                {"stock": firestore.Increment(int(item.get("count", 1)))}
            )
            print(f"[STOCK] Restored {item.get('count', 1)} unit(s) of '{product_id}'")
        except Exception as e:
            print(f"[ERROR] Could not restore stock for '{product_id}': {e}")

@firestore.transactional
def _lock_order(transaction, order_ref):
    """Atomically move pending -> dispensing. Returns order data, or None if not vendable."""
    snap = order_ref.get(transaction=transaction)
    if not snap.exists:
        print(f"[REJECT] Order '{order_ref.id}' does not exist in database.")
        return None
    data = snap.to_dict()
    if data.get("status") != "pending":
        print(f"[REJECT] Order '{order_ref.id}' has status '{data.get('status')}'. Cannot vend.")
        return None
    transaction.update(order_ref, {
        "status": "dispensing",
        "dispensedAt": firestore.SERVER_TIMESTAMP,
    })
    return data

def remaining_items(products, delivered_counts):
    """Items (with count reduced by what already dropped) that are still owed to the customer."""
    owed = []
    for i, item in enumerate(products):
        done = delivered_counts[i] if i < len(delivered_counts) else 0
        left = int(item.get("count", 1)) - int(done)
        if left > 0:
            owed.append({**item, "count": left})
    return owed

def settle_failed_order(order_ref, order_id, order_data, delivered_counts, failure_reason, ambiguous=False):
    """Marks the order jammed, refunds what was not delivered and puts that stock back."""
    user_id = order_data.get("userId", "")
    amount = order_data.get("amount", 0)
    undelivered = remaining_items(order_data.get("products", []), delivered_counts)

    order_ref.update({
        "status": "jammed",
        "error": failure_reason,
        "updatedAt": firestore.SERVER_TIMESTAMP
    })
    try:
        owed = sum(float(i["price"]) * int(i.get("count", 1)) for i in undelivered)
    except (KeyError, TypeError, ValueError):
        owed = amount if not any(delivered_counts) else 0  # legacy order: full refund only if nothing dropped
    if user_id and owed > 0:
        refund_credits(user_id, owed, order_id, failure_reason, ambiguous)
    restore_stock(undelivered)

def process_order(order_id, link):
    print("\n" + "=" * 60)
    print(f"[SCAN] Scanned Order ID: {order_id}")
    order_ref = db.collection("orders").document(order_id)

    # Make sure the machine can actually vend BEFORE locking the order; otherwise leave it
    # pending so the customer can simply scan again once the Arduino is back.
    ser = link.get()
    if ser is None:
        print("[REJECT] Arduino not connected; order left pending, please scan again shortly.")
        print("=" * 60 + "\n")
        return

    try:
        order_data = _lock_order(db.transaction(), order_ref)
    except Exception as e:
        print(f"[ERROR] Could not read/lock order: {e}")
        print("=" * 60 + "\n")
        return
    if order_data is None:
        print("=" * 60 + "\n")
        return

    products = order_data.get("products", [])
    delivered_counts = [0] * len(products)  # bags confirmed dropped, per product line

    all_vended = True
    failure_reason = ""
    ambiguous = False

    try:
        for idx, item in enumerate(products):
            slot = str(item.get("position", "")).upper()
            count = int(item.get("count", 1))
            name = item.get("name", "Chips")
            if not slot:
                all_vended, failure_reason = False, "Order item has no slot position"
                break

            print(f"[DISPATCH] Vending {count}x '{name}' from Slot [{slot}]...")
            result, delivered = send_arduino_dispense(ser, slot, count)
            delivered_counts[idx] = delivered

            # Persist progress so a crash/power loss can be settled correctly on restart
            try:
                order_ref.update({"deliveredCounts": delivered_counts})
            except Exception as e:
                print(f"[WARN] Could not persist vend progress: {e}")

            if result == "SUCCESS":
                print(f"[OK] Slot {slot} dispensed {count} bag(s).")
            else:
                all_vended = False
                failure_reason = f"Slot {slot} status: {result} ({delivered}/{count} bag(s) delivered)"
                # No result line arrived in time: a bag may have dropped without being counted
                ambiguous = result == "TIMEOUT"
                if result in ("ERROR", "TIMEOUT"):
                    link.drop()  # force a fresh connection on the next scan
                print(f"[ALERT] {failure_reason}")
                break
    except Exception as e:
        all_vended, failure_reason = False, f"Unexpected error: {e}"
        print(f"[ALERT] {failure_reason}")

    # Finalize order status & refund on failure (order must never stay stuck in 'dispensing')
    try:
        if all_vended:
            order_ref.update({
                "status": "completed",
                "completedAt": firestore.SERVER_TIMESTAMP
            })
            print(f"[COMPLETE] Order '{order_id}' successfully finished!")
        else:
            settle_failed_order(order_ref, order_id, order_data, delivered_counts, failure_reason, ambiguous)
    except Exception as e:
        print(f"[ERROR] Failed to finalize order '{order_id}': {e}")

    print("=" * 60 + "\n")

def recover_stuck_orders():
    """
    Orders left in 'dispensing' by a crash or power loss are settled on startup: whatever the
    persisted progress shows as not delivered (including the bag that was mid-drop) is refunded.
    """
    try:
        stuck = list(db.collection("orders").where("status", "==", "dispensing").stream())
    except Exception as e:
        print(f"[RECOVERY] Could not query stuck orders: {e}")
        return
    for snap in stuck:
        data = snap.to_dict() or {}
        print(f"[RECOVERY] Settling order '{snap.id}' left in 'dispensing' by a restart.")
        try:
            settle_failed_order(
                snap.reference, snap.id, data, data.get("deliveredCounts") or [],
                "Controller restarted mid-vend", ambiguous=True,
            )
        except Exception as e:
            print(f"[RECOVERY] Failed to settle '{snap.id}': {e}")

def main():
    print("=======================================================")
    print("      SNACKER HARDWARE VENDING DAEMON STARTING         ")
    print("=======================================================")

    # 1. Connect to Arduino Mega over Serial (reconnected automatically if it drops later)
    link = ArduinoLink()
    while link.get() is None:
        print("[WAIT] Waiting for Arduino Mega...")
        time.sleep(3)

    recover_stuck_orders()

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
                                process_order(order_id, link)
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
