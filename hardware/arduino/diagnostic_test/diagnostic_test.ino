/*
 * =====================================================================
 *  SNACKER Vending Machine - Six-Motor Diagnostic Bench Test
 *  Board: Arduino Mega 2560
 * =====================================================================
 *  Serial Monitor commands (115200 baud):
 *    1       Test motor 1 (A1 - Pin 22)
 *    2       Test motor 2 (A2 - Pin 24)
 *    3       Test motor 3 (A3 - Pin 26)
 *    4       Test motor 4 (B1 - Pin 28)
 *    5       Test motor 5 (B2 - Pin 30)
 *    6       Test motor 6 (B3 - Pin 32)
 *    ALL     Test all six motors sequentially
 *    STOP    Stop all motor outputs immediately
 * =====================================================================
 */

struct MotorChannel {
  const char* name;
  uint8_t pin;
};

MotorChannel motors[] = {
  {"A1", 22},
  {"A2", 24},
  {"A3", 26},
  {"B1", 28},
  {"B2", 30},
  {"B3", 32}
};

const uint8_t MOTOR_COUNT = sizeof(motors) / sizeof(motors[0]);

// Opto-isolated relay boards are Active LOW (LOW = Energized, HIGH = Cut)
const uint8_t MOTOR_ON  = LOW;
const uint8_t MOTOR_OFF = HIGH;

// Short test pulse duration (ms)
const unsigned long TEST_TIME_MS = 1000;

void stopAllMotors() {
  for (uint8_t i = 0; i < MOTOR_COUNT; i++) {
    digitalWrite(motors[i].pin, MOTOR_OFF);
  }
}

void testMotor(uint8_t index) {
  if (index >= MOTOR_COUNT) {
    Serial.println("ERROR: Invalid motor index");
    return;
  }

  stopAllMotors();

  Serial.print("Testing motor ");
  Serial.print(motors[index].name);
  Serial.print(" on Arduino Mega digital pin ");
  Serial.println(motors[index].pin);

  digitalWrite(motors[index].pin, MOTOR_ON);
  delay(TEST_TIME_MS);
  digitalWrite(motors[index].pin, MOTOR_OFF);

  Serial.print("Test pulse complete for: ");
  Serial.println(motors[index].name);
}

void testAllMotors() {
  Serial.println("Starting sequential motor test...");

  for (uint8_t i = 0; i < MOTOR_COUNT; i++) {
    testMotor(i);
    delay(1000);
  }

  stopAllMotors();
  Serial.println("All six test pulses completed.");
}

void setup() {
  Serial.begin(115200);

  // Guarantee all relay pins start in safe OFF state
  for (uint8_t i = 0; i < MOTOR_COUNT; i++) {
    digitalWrite(motors[i].pin, MOTOR_OFF);
    pinMode(motors[i].pin, OUTPUT);
  }

  stopAllMotors();

  Serial.println();
  Serial.println("==========================================");
  Serial.println("   SNACKER MOTOR DIAGNOSTIC TEST READY    ");
  Serial.println("==========================================");
  Serial.println("Enter 1-6 to test an individual motor.");
  Serial.println("Enter ALL to test all motors.");
  Serial.println("Enter STOP to stop all outputs.");
}

void loop() {
  if (Serial.available() > 0) {
    String command = Serial.readStringUntil('\n');
    command.trim();
    command.toUpperCase();

    if (command == "STOP") {
      stopAllMotors();
      Serial.println("All motor outputs turned OFF");
    }
    else if (command == "ALL") {
      testAllMotors();
    }
    else if (command.length() == 1 &&
             command[0] >= '1' &&
             command[0] <= '6') {
      testMotor(command[0] - '1');
    }
    else {
      Serial.println("Invalid command. Use 1-6, ALL, or STOP.");
    }
  }
}
