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
 * =====================================================================
 */

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
  pinMode(PIN_IR_DROP_SENSOR, INPUT_PULLUP);
  attachInterrupt(digitalPinToInterrupt(PIN_IR_DROP_SENSOR), isr_snackDropDetected, FALLING);

  // Power-on acoustic confirmation
  soundBuzzer(2, 70);
  Serial.println("SYSTEM:READY_CHANNELS_6");
}

// ---------------------------------------------------------------------
// DISPENSE CORE ROUTINE
// ---------------------------------------------------------------------
bool executeMotorCycle(int pin, String slotId) {
  digitalWrite(PIN_LED_GREEN, HIGH);
  digitalWrite(PIN_LED_RED, LOW);
  g_snackDropped = false;

  Serial.println("STATUS:DISPENSING:" + slotId);

  // Energize Motor Relay
  digitalWrite(pin, RELAY_ACTIVE);

  unsigned long startTime = millis();
  bool dropConfirmed = false;

  // Run motor until IR beam break triggers or watchdog timer expires
  while (millis() - startTime < MAX_MOTOR_RUN_MS) {
    if (g_snackDropped) {
      // Snack fell through the drop chute!
      delay(MIN_DROP_CLEAR_MS);
      dropConfirmed = true;
      break;
    }
    delay(10);
  }

  // Immediate motor cutoff (prevents coil overheating or gearbox stripping)
  digitalWrite(pin, RELAY_INACTIVE);
  digitalWrite(PIN_LED_GREEN, LOW);

  if (dropConfirmed) {
    soundBuzzer(1, 120);
    Serial.println("DONE:" + slotId + ":VERIFIED");
    return true;
  } else {
    // Motor ran full cycle but IR beam was not tripped (item jammed or slot empty)
    digitalWrite(PIN_LED_RED, HIGH);
    soundBuzzer(3, 100);
    Serial.println("WARN:" + slotId + ":NO_DROP_DETECTED");
    digitalWrite(PIN_LED_RED, LOW);
    return false;
  }
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
    Serial.println(digitalRead(PIN_IR_DROP_SENSOR) == LOW ? "BLOCKED" : "CLEAR");
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
        executeMotorCycle(pin, slotId);
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
