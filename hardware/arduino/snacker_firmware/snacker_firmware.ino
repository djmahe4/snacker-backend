/*
 * =====================================================================
 *  SNACKER 6-SLOT CHIP VENDING MACHINE - ARDUINO MEGA 2560 CONTROLLER
 * =====================================================================
 *  Channels:
 *    A1 -> Pin 22 (Slot 1: e.g., Lays Classic)
 *    A2 -> Pin 24 (Slot 2: e.g., Doritos Nacho)
 *    A3 -> Pin 26 (Slot 3: e.g., Oreo Cookies)
 *    B1 -> Pin 28 (Slot 4: e.g., Snickers Bar)
 *    B2 -> Pin 30 (Slot 5: e.g., Coca-Cola)
 *    B3 -> Pin 32 (Slot 6: e.g., KitKat)
 *
 *  Hardware Features:
 *   - Opto-isolated relay driver (prevents Back-EMF / reverse current spikes)
 *   - Hardware Interrupt-driven IR Drop Detection Sensor (Digital Pin 2)
 *   - Anti-jamming runtime watchdog (auto-cuts motor if item jams or timeout expires)
 *   - Diagnostic self-test suite (STATUS, PING, TEST_ALL)
 *   - Status indicators: Green LED (12), Red LED (11), Active Buzzer (10)
 *
 *  CLI diagnostics (no LEDs needed):
 *    PINS    -> level each relay pin is actually at, and ON/OFF meaning
 *    VCC     -> the Mega's own supply voltage in mV (internal bandgap)
 *    UPTIME  -> ms since this boot (small again = the Mega reset)
 *    DIAG    -> boot count, last reset cause, uptime, VCC and PINS in one go
 *  On every boot it also prints: INFO:BOOT count=<n> reset=<cause> vcc_mv=<mV>
 * =====================================================================
 */

#include <EEPROM.h>
#include <avr/wdt.h>

// Reset cause, captured before Arduino's init() runs (standard avr-libc pattern).
// Some bootloaders clear MCUSR first; then this reads 0 and the cause shows as UNKNOWN.
uint8_t g_resetFlags __attribute__((section(".noinit")));
void captureResetFlags(void) __attribute__((naked, used, section(".init3")));
void captureResetFlags(void) {
  g_resetFlags = MCUSR;
  MCUSR = 0;
  wdt_disable();
}

const int EEPROM_BOOT_COUNT_ADDR = 0;   // uint32_t boot counter (survives power loss)
unsigned long g_bootCount = 0;

struct SlotChannel {
  const char* id;
  int relayPin;
};

// 6 Chip Slots mapped to Arduino Mega Digital Pins
const SlotChannel SLOTS[] = {
  {"A1", 22},  // Slot 1
  {"A2", 24},  // Slot 2
  {"A3", 26},  // Slot 3
  {"B1", 28},  // Slot 4
  {"B2", 30},  // Slot 5
  {"B3", 32}   // Slot 6
};
const int TOTAL_SLOTS = sizeof(SLOTS) / sizeof(SLOTS[0]);

// --- SENSORS & PERIPHERALS ---
const int PIN_IR_DROP_SENSOR = 2;   // INT.0: Hardware interrupt (Active LOW on beam break)
const int PIN_LED_GREEN      = 12;  // Dispensing / Success Indicator
const int PIN_LED_RED        = 11;  // Error / Jamming / Sensor Fault Indicator
const int PIN_BUZZER         = 10;  // 5V Active Acoustic Feedback

// --- TIMING & SAFETY WATCHDOGS ---
const unsigned long MAX_MOTOR_RUN_MS  = 4500;  // Max motor cycle before timeout cutoff (anti-burnout)
const unsigned long MIN_DROP_CLEAR_MS = 300;   // Debounce clearance after snack drops through chute

// --- DROP VERIFICATION MODE ---
// No IR sensor installed -> false: each bag is a fixed timed motor run, always reported as success.
// IR sensor wired to pin 2  -> true: motor runs until the beam breaks (or MAX_MOTOR_RUN_MS expires).
const bool USE_IR_SENSOR = false;
const unsigned long TIMED_RUN_MS = 2500;       // Motor run per bag when USE_IR_SENSOR is false. TUNE to one full coil turn.

// Relay Boards are typically Active-LOW (LOW = Energized, HIGH = Cut)
const int RELAY_ACTIVE   = LOW;
const int RELAY_INACTIVE = HIGH;

// Volatile flag toggled by hardware interrupt when snack breaks IR beam
volatile bool g_snackDropped = false;

// ---------------------------------------------------------------------
// INTERRUPT SERVICE ROUTINE (ISR)
// ---------------------------------------------------------------------
void isr_snackDropDetected() {
  g_snackDropped = true;
}

// ---------------------------------------------------------------------
// HELPER FUNCTIONS
// ---------------------------------------------------------------------
int findSlotPin(String slotId) {
  for (int i = 0; i < TOTAL_SLOTS; i++) {
    if (slotId.equalsIgnoreCase(SLOTS[i].id)) {
      return SLOTS[i].relayPin;
    }
  }
  return -1;
}

// Supply voltage in mV, by measuring the internal 1.1 V bandgap against AVcc.
// Accuracy about +/-10% (bandgap tolerance): enough to see a sag or brownout.
long readVccMv() {
  ADMUX = _BV(REFS0) | _BV(MUX4) | _BV(MUX3) | _BV(MUX2) | _BV(MUX1);  // AVcc ref, bandgap input
  ADCSRB &= ~_BV(MUX5);
  delay(2);                                  // let the reference settle
  uint16_t result = 0;
  for (int i = 0; i < 2; i++) {              // first conversion after a mux change is discarded
    ADCSRA |= _BV(ADSC);
    while (bit_is_set(ADCSRA, ADSC)) {}
    uint8_t low = ADCL;                      // ADCL must be read before ADCH
    uint8_t high = ADCH;
    result = ((uint16_t)high << 8) | low;
  }
  return result ? 1125300L / result : -1;    // 1.1 V * 1023 * 1000
}

String resetCause() {
  if (g_resetFlags == 0) return "UNKNOWN";
  String s = "";
  if (g_resetFlags & _BV(PORF))  s += "POWER_ON+";
  if (g_resetFlags & _BV(EXTRF)) s += "EXTERNAL+";   // reset pin: USB port open (DTR) or button
  if (g_resetFlags & _BV(BORF))  s += "BROWNOUT+";   // supply dropped below the brown-out level
  if (g_resetFlags & _BV(WDRF))  s += "WATCHDOG+";
  if (s.length() == 0) return "OTHER";
  return s.substring(0, s.length() - 1);
}

void printPins() {
  Serial.print("INFO:PINS ");
  for (int i = 0; i < TOTAL_SLOTS; i++) {
    int level = digitalRead(SLOTS[i].relayPin);  // actual pin level, also for OUTPUT pins
    Serial.print(SLOTS[i].id);
    Serial.print(level == HIGH ? "=HIGH(" : "=LOW(");
    Serial.print(level == RELAY_ACTIVE ? "ON)" : "OFF)");
    if (i < TOTAL_SLOTS - 1) Serial.print(" ");
  }
  Serial.println();
}

void soundBuzzer(int pulses, int durationMs) {
  for (int i = 0; i < pulses; i++) {
    digitalWrite(PIN_BUZZER, HIGH);
    delay(durationMs);
    digitalWrite(PIN_BUZZER, LOW);
    if (pulses > 1) delay(60);
  }
}

// ---------------------------------------------------------------------
// SETUP
// ---------------------------------------------------------------------
void setup() {
  Serial.begin(115200);

  // Status indicators
  pinMode(PIN_LED_GREEN, OUTPUT);
  pinMode(PIN_LED_RED, OUTPUT);
  pinMode(PIN_BUZZER, OUTPUT);
  digitalWrite(PIN_LED_GREEN, LOW);
  digitalWrite(PIN_LED_RED, LOW);
  digitalWrite(PIN_BUZZER, LOW);

  // Safe Relay Initialization (guarantee all 6 motors are OFF on boot)
  for (int i = 0; i < TOTAL_SLOTS; i++) {
    digitalWrite(SLOTS[i].relayPin, RELAY_INACTIVE);
    pinMode(SLOTS[i].relayPin, OUTPUT);
  }

  // Setup IR Drop Beam Sensor on Interrupt Pin 2
  if (USE_IR_SENSOR) {
    pinMode(PIN_IR_DROP_SENSOR, INPUT_PULLUP);
    attachInterrupt(digitalPinToInterrupt(PIN_IR_DROP_SENSOR), isr_snackDropDetected, FALLING);
  }

  // Count boots in EEPROM so resets are visible from the CLI even without LEDs
  EEPROM.get(EEPROM_BOOT_COUNT_ADDR, g_bootCount);
  if (g_bootCount == 0xFFFFFFFFUL) g_bootCount = 0;  // erased EEPROM
  g_bootCount++;
  EEPROM.put(EEPROM_BOOT_COUNT_ADDR, g_bootCount);

  // Power-on acoustic confirmation
  soundBuzzer(2, 70);
  Serial.println("SYSTEM:READY_CHANNELS_6");
  Serial.print("INFO:BOOT count=");
  Serial.print(g_bootCount);
  Serial.print(" reset=");
  Serial.print(resetCause());
  Serial.print(" vcc_mv=");
  Serial.println(readVccMv());
}

// ---------------------------------------------------------------------
// DISPENSE CORE ROUTINE
// ---------------------------------------------------------------------
// Non-blocking emergency stop check used while a motor is running.
// Returns true if a STOP command arrived (any other input is discarded).
bool stopRequested() {
  while (Serial.available() > 0) {
    String line = Serial.readStringUntil('\n');
    line.trim();
    if (line == "STOP") return true;
  }
  return false;
}

bool executeMotorCycle(int pin, String slotId) {
  digitalWrite(PIN_LED_GREEN, HIGH);
  digitalWrite(PIN_LED_RED, LOW);
  g_snackDropped = false;

  Serial.println("STATUS:DISPENSING:" + slotId);

  unsigned long runLimit = USE_IR_SENSOR ? MAX_MOTOR_RUN_MS : TIMED_RUN_MS;
  bool dropConfirmed = false;
  bool aborted = false;

  // Energize Motor Relay
  digitalWrite(pin, RELAY_ACTIVE);
  unsigned long startTime = millis();

  while (millis() - startTime < runLimit) {
    if (stopRequested()) { aborted = true; break; }
    if (USE_IR_SENSOR && g_snackDropped) {
      delay(MIN_DROP_CLEAR_MS);   // let the snack clear the chute
      dropConfirmed = true;
      break;
    }
    delay(10);
  }

  // Immediate motor cutoff (prevents coil overheating or gearbox stripping)
  digitalWrite(pin, RELAY_INACTIVE);
  digitalWrite(PIN_LED_GREEN, LOW);

  if (aborted) {
    Serial.println("WARN:" + slotId + ":ABORTED");
    return false;
  }

  if (!USE_IR_SENSOR) {
    // Timed mode: no way to verify the drop, a completed run counts as success.
    soundBuzzer(1, 120);
    Serial.println("DONE:" + slotId + ":TIMED");
    return true;
  }

  if (dropConfirmed) {
    soundBuzzer(1, 120);
    Serial.println("DONE:" + slotId + ":VERIFIED");
    return true;
  }

  // Motor ran full cycle but IR beam was not tripped (item jammed or slot empty)
  digitalWrite(PIN_LED_RED, HIGH);
  soundBuzzer(3, 100);
  Serial.println("WARN:" + slotId + ":NO_DROP_DETECTED");
  digitalWrite(PIN_LED_RED, LOW);
  return false;
}

// ---------------------------------------------------------------------
// COMMAND PARSER
// ---------------------------------------------------------------------
void processIncomingCommand(String cmd) {
  cmd.trim();
  if (cmd.length() == 0) return;

  // 1. Diagnostic Ping
  if (cmd == "PING") {
    Serial.println("PONG");
    return;
  }

  // 2. Diagnostic Status
  if (cmd == "STATUS") {
    Serial.print("INFO:SLOTS=");
    for (int i = 0; i < TOTAL_SLOTS; i++) {
      Serial.print(SLOTS[i].id);
      if (i < TOTAL_SLOTS - 1) Serial.print(",");
    }
    Serial.print(";DROP_SENSOR=");
    if (!USE_IR_SENSOR) Serial.println("DISABLED");
    else Serial.println(digitalRead(PIN_IR_DROP_SENSOR) == LOW ? "BLOCKED" : "CLEAR");
    return;
  }

  // 2b. CLI diagnostics
  if (cmd == "PINS") {
    printPins();
    return;
  }
  if (cmd == "VCC") {
    Serial.print("INFO:VCC_MV=");
    Serial.println(readVccMv());
    return;
  }
  if (cmd == "UPTIME") {
    Serial.print("INFO:UPTIME_MS=");
    Serial.println(millis());
    return;
  }
  if (cmd == "DIAG") {
    Serial.print("INFO:DIAG boots=");
    Serial.print(g_bootCount);
    Serial.print(" reset=");
    Serial.print(resetCause());
    Serial.print(" uptime_ms=");
    Serial.print(millis());
    Serial.print(" vcc_mv=");
    Serial.println(readVccMv());
    printPins();
    return;
  }

  // 3. Hardware Self-Test (Tests all 6 relays sequentially for 600ms)
  if (cmd == "TEST_ALL") {
    Serial.println("INFO:STARTING_SELF_TEST");
    for (int i = 0; i < TOTAL_SLOTS; i++) {
      Serial.println(String("TESTING:") + SLOTS[i].id);
      digitalWrite(SLOTS[i].relayPin, RELAY_ACTIVE);
      delay(600);
      digitalWrite(SLOTS[i].relayPin, RELAY_INACTIVE);
      delay(400);
    }
    Serial.println("INFO:SELF_TEST_COMPLETE");
    return;
  }

  // 4. Dispense Command: DISPENSE:<SLOT>:<QTY>
  if (cmd.startsWith("DISPENSE:")) {
    int firstColon = cmd.indexOf(':');
    int secondColon = cmd.indexOf(':', firstColon + 1);

    if (firstColon != -1 && secondColon != -1) {
      String slotId = cmd.substring(firstColon + 1, secondColon);
      int count = cmd.substring(secondColon + 1).toInt();
      if (count <= 0) count = 1;

      int pin = findSlotPin(slotId);
      if (pin == -1) {
        Serial.println("ERROR:UNKNOWN_SLOT_" + slotId);
        return;
      }

      for (int i = 0; i < count; i++) {
        // Every bag reports exactly one DONE/WARN line so the Pi can count results.
        bool ok = executeMotorCycle(pin, slotId);
        if (!ok) {
          // Abort remaining bags; report them as not dispensed so the Pi stops waiting.
          for (int r = i + 1; r < count; r++) Serial.println("WARN:" + slotId + ":SKIPPED");
          break;
        }
        if (i < count - 1) delay(1200); // Inter-bag cooldown pause
      }
      return;
    }
  }

  // 5. Emergency Stop
  if (cmd == "STOP") {
    for (int i = 0; i < TOTAL_SLOTS; i++) {
      digitalWrite(SLOTS[i].relayPin, RELAY_INACTIVE);
    }
    digitalWrite(PIN_LED_GREEN, LOW);
    digitalWrite(PIN_LED_RED, HIGH);
    Serial.println("ALL_MOTORS_STOPPED");
    digitalWrite(PIN_LED_RED, LOW);
    return;
  }

  Serial.println("ERROR:INVALID_SYNTAX");
}

void loop() {
  if (Serial.available() > 0) {
    String command = Serial.readStringUntil('\n');
    processIncomingCommand(command);
  }
}
