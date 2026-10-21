import crypto from "node:crypto";

const WEBHOOK_SECRET = process.env.YOUPI_WEBHOOK_SECRET || "snacker_youpi_secret_8899";
const BACKEND_URL = process.env.BACKEND_URL || "http://localhost:5000";

async function runWebhookTest() {
  console.log("🧪 Testing YouPI-PG Webhook HMAC Verification...");

  const payload = {
    event: "SUCCESS",
    orderId: "WAL-test1234",
    amount: "50.00",
    utr: "UPI123456789012",
    completedAt: Date.now(),
    metadata: {
      userId: "user_test_student_01",
    },
  };

  const rawBody = JSON.stringify(payload);
  const signature = "sha256=" + crypto
    .createHmac("sha256", WEBHOOK_SECRET)
    .update(rawBody)
    .digest("hex");

  console.log("  Payload:", payload);
  console.log("  Generated HMAC Signature:", signature);

  try {
    // 1. Test Valid Signature
    console.log("\n1️⃣  Sending valid signature request...");
    const res = await fetch(`${BACKEND_URL}/api/youpi-webhook`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "x-gateway-signature": signature,
        "x-gateway-timestamp": Date.now().toString(),
      },
      body: rawBody,
    });

    const data = await res.json();
    console.log(`   Response (${res.status}):`, data);

    // 2. Test Invalid/Tampered Signature
    console.log("\n2️⃣  Sending tampered signature request (should be rejected with 401)...");
    const badRes = await fetch(`${BACKEND_URL}/api/youpi-webhook`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "x-gateway-signature": "sha256=invalid_tampered_signature_hex",
        "x-gateway-timestamp": Date.now().toString(),
      },
      body: rawBody,
    });

    const badData = await badRes.json();
    console.log(`   Response (${badRes.status}):`, badData);

    if (badRes.status === 401) {
      console.log("\n✅ Security check passed: Tampered signatures correctly rejected!");
    } else {
      console.warn("\n⚠️ Security check warning: Bad signature was not rejected with 401.");
    }
  } catch (error) {
    console.error("❌ Connection failed (make sure backend server is running on port 5000):", error.message);
  }
}

runWebhookTest();
