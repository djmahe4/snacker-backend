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

 Logging (stderr, so systemd/journald gets every line immediately):
   LOG_LEVEL=DEBUG|INFO|WARNING   default INFO; DEBUG adds raw serial traffic,
                                  device enumeration, Firestore and timing details
   LOG_FILE=/path/to/vending.log  optional rotating file copy (1 MB x 3)
   View: journalctl -u snacker-vending -f
=============================================================================
"""

import logging
import logging.handlers
import os
import sys
import threading
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


def setup_logging():
    level = getattr(logging, os.environ.get("LOG_LEVEL", "INFO").upper(), logging.INFO)
    # journald already timestamps each line; add our own only when not running under systemd
    under_systemd = bool(os.environ.get("JOURNAL_STREAM"))
    fmt = "%(levelname)-7s %(message)s" if under_systemd else "%(asctime)s %(levelname)-7s %(message)s"
    handlers = [logging.StreamHandler(sys.stderr)]
    log_file = os.environ.get("LOG_FILE")
    if log_file:
        file_handler = logging.handlers.RotatingFileHandler(log_file, maxBytes=1_000_000, backupCount=3)
        file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(message)s"))
        handlers.append(file_handler)
    logging.basicConfig(level=level, format=fmt, handlers=handlers)
    # Google client libraries are very chatty at DEBUG
    for noisy in ("google", "urllib3", "grpc"):
        logging.getLogger(noisy).setLevel(max(level, logging.INFO))
    return logging.getLogger("vending")


log = setup_logging()

# Only one reader of the serial port at a time: a dispense holds this lock while it waits for
# DONE/WARN; the idle monitor reads the port only when it can take the lock without waiting.
serial_lock = threading.Lock()
READY_BANNER = "SYSTEM:READY"           # printed by the firmware's setup(), i.e. on every Mega reset
EXPECTED_RESET_WINDOW_S = 6             # opening the port resets the Mega once; a banner this soon is expected


def short_id(order_id):
    """Order IDs are the vending QR payload (a bearer credential), so logs show a short form."""
    order_id = str(order_id or "")
    return order_id if len(order_id) <= 6 else f"{order_id[:4]}…{order_id[-2:]}"


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
            log.info("[FIREBASE] Initialized with credentials from %s (project %s)",
                     FIREBASE_KEY_PATH, cred.project_id)
        else:
            # Fallback to default application credentials if running in GCP environment
            log.warning("[FIREBASE] %s not found; falling back to application default credentials",
                        FIREBASE_KEY_PATH)
            firebase_admin.initialize_app()
    return firestore.client()

db = init_firebase()

def find_arduino_port():
    """Detects Arduino Mega on USB serial ports."""
    ports = serial.tools.list_ports.comports()
    for p in ports:
        log.debug("[SERIAL] Candidate port %s (%s)", p.device, p.description)
    for p in ports:
        desc = (p.description or "").lower()
        dev = (p.device or "").lower()
        if "arduino" in desc or "ch340" in desc or "acm" in dev or "usb" in dev:
            return p.device
    log.debug("[SERIAL] No matching port found, defaulting to /dev/ttyACM0")
    return "/dev/ttyACM0"

def find_scanner_device():
    """Finds Fingers QuickScan-Omni-W106 USB HID device."""
    try:
        devices = []
        for path in evdev.list_devices():
            d = evdev.InputDevice(path)
            keys = d.capabilities().get(ecodes.EV_KEY, [])
            is_keyboard = ecodes.KEY_ENTER in keys and ecodes.KEY_A in keys
            log.debug("[SCANNER] Input device %s '%s' keyboard-like=%s", d.path, d.name, is_keyboard)
            if is_keyboard:  # skips mice / consumer-control nodes
                devices.append(d)
        for dev in devices:
            name = dev.name.lower()
            if any(k in name for k in ["scanner", "barcode", "omni", "fingers", "w106", "hid"]):
                return dev
        # Fallback to the first available keyboard device
        for dev in devices:
            if "keyboard" in dev.name.lower():
                log.warning("[SCANNER] No scanner-named device; falling back to keyboard '%s'", dev.name)
                return dev
    except Exception:
        log.exception("[SCANNER] Device enumeration failed")
    return None

def send_arduino_dispense(ser, slot, count=1, run_ms=MOTOR_RUN_MS):
    """
    Sends DISPENSE:<slot>:<count> and waits for one result line per bag:
      - 'DONE:<SLOT>:TIMED' / 'DONE:<SLOT>:VERIFIED' -> bag dispensed
      - 'WARN:<SLOT>:...'                            -> bag failed (jam / abort / skipped)
    Returns SUCCESS only if every bag succeeded, otherwise JAMMED / TIMEOUT / ERROR.
    """
    with serial_lock:  # keep the idle monitor from consuming our DONE/WARN lines
        return _send_arduino_dispense_locked(ser, slot, count, run_ms)


def _send_arduino_dispense_locked(ser, slot, count, run_ms):
    try:
        stale = ser.in_waiting
        if stale:
            log.debug("[SERIAL] Discarding %d stale byte(s) before command", stale)
        ser.reset_input_buffer()
        cmd = f"DISPENSE:{slot}:{count}\n"
        ser.write(cmd.encode("utf-8"))
        log.info("[ARDUINO ->] %s", cmd.strip())

        # Each bag: motor run + 1.2s cooldown, plus a safety margin
        timeout = count * (run_ms / 1000.0 + 1.5) + 5
        results = 0
        start = time.time()
        while time.time() - start < timeout:
            if ser.in_waiting > 0:
                line = ser.readline().decode("utf-8", errors="ignore").strip()
                if not line:
                    continue
                elapsed = time.time() - start
                if line.startswith(("ERROR:", "WARN:", "DONE:")):
                    log.info("[ARDUINO <-] %s (+%.1fs)", line, elapsed)
                else:
                    log.debug("[ARDUINO <-] %s (+%.1fs)", line, elapsed)
                if line.startswith("ERROR:"):
                    return "ERROR"
                if line.startswith("WARN:"):
                    return "JAMMED"
                if line.startswith("DONE:"):
                    results += 1
                    if results >= count:
                        return "SUCCESS"
            else:
                time.sleep(0.05)
        log.warning("[ARDUINO] No result after %.1fs (%d/%d bag(s) confirmed) for slot %s",
                    timeout, results, count, slot)
        return "TIMEOUT"
    except Exception:
        log.exception("[SERIAL] Communication error while dispensing slot %s", slot)
        return "ERROR"

def start_serial_monitor(ser, opened_at):
    """
    Logs everything the Mega prints while no dispense is running. The key signal is the
    firmware's boot banner: it only appears when the Mega resets, and while it resets its relay
    pins float (motors can run uncommanded). Repeated resets point at power or at another
    process opening the port.
    """
    state = {"resets": 0, "failing": False}

    def monitor():
        while True:
            if serial_lock.acquire(blocking=False):
                try:
                    while ser.in_waiting > 0:
                        line = ser.readline().decode("utf-8", errors="ignore").strip()
                        if not line:
                            continue
                        if line.startswith(READY_BANNER):
                            since_open = time.time() - opened_at
                            if state["resets"] == 0 and since_open < EXPECTED_RESET_WINDOW_S:
                                log.info("[ARDUINO] Firmware ready after port open (%s)", line)
                            else:
                                log.warning(
                                    "[ARDUINO] Mega RESET detected (#%d, %.0fs after port open). "
                                    "Relay pins float during a reset, so motors may run uncommanded. "
                                    "Check the Mega/relay power supply and whether another process "
                                    "opens the port (sudo fuser -v %s).",
                                    state["resets"] + 1, since_open, ser.port)
                            state["resets"] += 1
                        elif line.startswith(("ERROR:", "WARN:", "DONE:", "STATUS:DISPENSING")):
                            # Motor activity or errors with no dispense in progress were not requested by us
                            log.warning("[ARDUINO <-] %s (no dispense in progress)", line)
                        else:
                            log.debug("[ARDUINO <-] %s (idle)", line)
                    if state["failing"]:
                        log.info("[SERIAL] Monitor reading %s again", ser.port)
                        state["failing"] = False
                except Exception as e:
                    if not state["failing"]:  # log once per outage, not every poll
                        log.error("[SERIAL] Monitor cannot read %s: %s (USB disconnected?)", ser.port, e)
                        state["failing"] = True
                finally:
                    serial_lock.release()
            time.sleep(0.2)

    threading.Thread(target=monitor, name="serial-monitor", daemon=True).start()
    log.info("[SERIAL] Idle monitor started: Mega resets and unrequested output will be logged")


def refund_credits(user_id, amount, order_id, reason):
    """Refunds credits back to student's wallet if a slot jams."""
    try:
        user_ref = db.collection("users").document(user_id)
        if user_ref.get().exists:
            user_ref.update({"credit": firestore.Increment(amount)})  # atomic, no lost updates
            log.info("[REFUND] Refunded %s pts to user '%s' for order %s (reason: %s)",
                     amount, user_id, short_id(order_id), reason)
        else:
            log.error("[REFUND] User '%s' not found; %s pts NOT refunded for order %s",
                      user_id, amount, short_id(order_id))

        # Log incident in Firestore errors collection for inventory audit
        db.collection("errors").document(f"jam_{order_id}_{int(time.time())}").set({
            "orderId": order_id,
            "userId": user_id,
            "refundedAmount": amount,
            "reason": reason,
            "timestamp": firestore.SERVER_TIMESTAMP,
            "context": "hardware_dispenser",
        })
    except Exception:
        log.exception("[REFUND] Refund failed for user '%s', order %s (%s pts)",
                      user_id, short_id(order_id), amount)

def restore_stock(items):
    """Returns reserved stock for items that were never delivered."""
    for item in items:
        product_id = item.get("productId")
        if not product_id:
            log.warning("[STOCK] Item without productId, stock not restored: %s", item.get("name"))
            continue
        try:
            db.collection("products").document(product_id).update(
                {"stock": firestore.Increment(int(item.get("count", 1)))}
            )
            log.info("[STOCK] Restored %s unit(s) of '%s'", item.get("count", 1), product_id)
        except Exception:
            log.exception("[STOCK] Could not restore stock for '%s'", product_id)

@firestore.transactional
def _lock_order(transaction, order_ref):
    """Atomically move pending -> dispensing. Returns order data, or None if not vendable."""
    snap = order_ref.get(transaction=transaction)
    if not snap.exists:
        log.warning("[REJECT] Order %s does not exist in database.", short_id(order_ref.id))
        return None
    data = snap.to_dict()
    if data.get("status") != "pending":
        log.warning("[REJECT] Order %s has status '%s'. Cannot vend.", short_id(order_ref.id), data.get("status"))
        return None
    transaction.update(order_ref, {
        "status": "dispensing",
        "dispensedAt": firestore.SERVER_TIMESTAMP,
    })
    return data

def process_order(order_id, ser):
    started = time.time()
    log.info("[SCAN] Order %s scanned", short_id(order_id))
    order_ref = db.collection("orders").document(order_id)

    try:
        order_data = _lock_order(db.transaction(), order_ref)
    except Exception:
        log.exception("[ORDER] Could not read/lock order %s", short_id(order_id))
        return
    if order_data is None:
        return

    user_id = order_data.get("userId", "")
    amount = order_data.get("amount", 0)
    products = order_data.get("products", [])
    log.info("[ORDER] %s locked: user '%s', amount %s, %d item(s)",
             short_id(order_id), user_id, amount, len(products))
    log.debug("[ORDER] %s items: %s", short_id(order_id),
              [(i.get("name"), i.get("position"), i.get("count")) for i in products])

    all_vended = True
    failure_reason = ""
    undelivered = list(products)  # items still owed to the customer; shrinks as each one vends

    try:
        for item in products:
            slot = str(item.get("position", "")).upper()
            count = int(item.get("count", 1))
            name = item.get("name", "Chips")
            if not slot:
                all_vended, failure_reason = False, "Order item has no slot position"
                log.error("[DISPATCH] %s: item '%s' has no slot position", short_id(order_id), name)
                break

            log.info("[DISPATCH] Vending %dx '%s' from slot %s", count, name, slot)
            t0 = time.time()
            result = send_arduino_dispense(ser, slot, count)

            if result == "SUCCESS":
                log.info("[OK] Slot %s dispensed %d bag(s) in %.1fs", slot, count, time.time() - t0)
                undelivered.remove(item)
            else:
                all_vended = False
                failure_reason = f"Slot {slot} status: {result}"
                log.error("[ALERT] %s: %s after %.1fs", short_id(order_id), failure_reason, time.time() - t0)
                break
    except Exception as e:
        all_vended, failure_reason = False, f"Unexpected error: {e}"
        log.exception("[ALERT] %s: unexpected error while dispensing", short_id(order_id))

    # Finalize order status & refund on failure (order must never stay stuck in 'dispensing')
    try:
        if all_vended:
            order_ref.update({
                "status": "completed",
                "completedAt": firestore.SERVER_TIMESTAMP
            })
            log.info("[COMPLETE] Order %s finished in %.1fs", short_id(order_id), time.time() - started)
        else:
            order_ref.update({
                "status": "jammed",
                "error": failure_reason,
                "updatedAt": firestore.SERVER_TIMESTAMP
            })
            # Refund only what was not delivered, and put that stock back
            try:
                owed = sum(float(i["price"]) * int(i.get("count", 1)) for i in undelivered)
            except (KeyError, TypeError, ValueError):
                owed = amount  # legacy order without per-item prices: refund in full
                log.warning("[REFUND] %s has no per-item prices; refunding full amount %s",
                            short_id(order_id), amount)
            log.info("[JAMMED] Order %s: %d undelivered item(s), refunding %s",
                     short_id(order_id), len(undelivered), owed)
            if user_id and owed > 0:
                refund_credits(user_id, owed, order_id, failure_reason)
            restore_stock(undelivered)
    except Exception:
        log.exception("[ORDER] Failed to finalize order %s (may be stuck in 'dispensing')", short_id(order_id))

def main():
    log.info("SNACKER vending daemon starting (log level %s, motor run %d ms)",
             logging.getLevelName(log.getEffectiveLevel()), MOTOR_RUN_MS)

    # 1. Connect to Arduino Mega over Serial
    ser = None
    while ser is None:
        port = find_arduino_port()
        try:
            ser = serial.Serial(port, SERIAL_BAUD, timeout=2)
            opened_at = time.time()
            log.info("[SERIAL] Opened %s (this resets the Mega once)", port)
            start_serial_monitor(ser, opened_at)
            time.sleep(2)  # Wait for Arduino bootloader reset
            with serial_lock:
                ser.write(b"STATUS\n")  # the reply (INFO:SLOTS=...) is logged by the monitor at DEBUG
            log.info("[SERIAL] Connected to Arduino Mega on %s", port)
        except Exception as e:
            if ser is not None:
                try:
                    ser.close()  # the monitor then reports the outage once and stays quiet
                except Exception:
                    pass
            ser = None
            log.warning("[WAIT] Waiting for Arduino Mega on %s... (%s)", port, e)
            time.sleep(3)

    # 2. Scanner Loop with Hotplug Resilience
    waiting_logged = False
    while True:
        scanner = find_scanner_device()
        if not scanner:
            if not waiting_logged:  # log once, not every 2 seconds
                log.warning("[WAIT] Waiting for Fingers QuickScan scanner to be connected...")
                waiting_logged = True
            time.sleep(2)
            continue
        waiting_logged = False

        try:
            # Exclusively grab device so raw keystrokes don't leak into terminal
            scanner.grab()
            log.info("[READY] Listening for QR codes on: %s (%s)", scanner.name, scanner.path)

            scanned_chars = []
            is_shift = False
            scan_started = None

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
                            duration = (time.time() - scan_started) if scan_started else 0.0
                            scan_started = None
                            log.debug("[SCAN] %d char(s) in %.2fs", len(order_id), duration)
                            if len(order_id) >= 6:  # Valid nanoid length
                                process_order(order_id, ser)
                            else:
                                log.warning("[SCAN] Ignored short scan (%d chars); not an order QR", len(order_id))
                        else:
                            if scan_started is None:
                                scan_started = time.time()
                            mapping = SCANCODE_MAP.get(data.scancode)
                            if mapping:
                                char = mapping[1] if is_shift else mapping[0]
                                scanned_chars.append(char)
                            else:
                                log.debug("[SCAN] Unmapped key code %s ignored", data.scancode)

        except (IOError, OSError) as e:
            log.warning("[DISCONNECT] Scanner unplugged or connection reset: %s", e)
            time.sleep(2)
        finally:
            try:
                scanner.ungrab()
            except Exception:
                pass

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log.info("Stopped by user")
    except Exception:
        log.exception("Vending daemon crashed; systemd will restart it")
        raise
