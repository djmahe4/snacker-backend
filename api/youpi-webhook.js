import { adminDb, FieldValue } from "../firebase/admin.js";
import crypto from "node:crypto";

const WEBHOOK_SECRET = process.env.YOUPI_WEBHOOK_SECRET; // required; server.js refuses to start without it

/**
 * YouPI-PG Dynamic UPI Webhook Handler.
 * Verifies HMAC-SHA256 signature, enforces idempotency, and credits user wallet atomically.
 */
export async function handleYoupiWebhook(req, res) {
  try {
    const rawBody = req.rawBody || JSON.stringify(req.body);
    const signature = req.headers["x-gateway-signature"] || "";
    const timestamp = Number(req.headers["x-gateway-timestamp"]);

    // Timestamp is mandatory; stale or missing deliveries are ignored (replay protection)
    if (!Number.isFinite(timestamp) || Math.abs(Date.now() - timestamp) > 5 * 60 * 1000) {
      return res.status(200).json({ received: true, note: "Stale delivery ignored" });
    }

    // Constant-time HMAC-SHA256 signature verification
    const expectedSig = "sha256=" + crypto
      .createHmac("sha256", WEBHOOK_SECRET)
      .update(rawBody)
      .digest("hex");

    const sigBuf = Buffer.from(signature);
    const expBuf = Buffer.from(expectedSig);

    if (sigBuf.length !== expBuf.length || !crypto.timingSafeEqual(sigBuf, expBuf)) {
      console.warn("[WEBHOOK ERROR] Invalid signature match");
      return res.status(401).json({ error: "Invalid webhook signature" });
    }

    const payload = typeof req.body === "string" ? JSON.parse(req.body) : req.body;

    // Process verified SUCCESS payment event
    if (payload.event === "SUCCESS") {
      const userId = payload.metadata?.userId;
      const amountInINR = Number(payload.amount);
      const orderId = payload.orderId;

      if (userId && amountInINR > 0) {
        const userRef = adminDb.collection("users").doc(userId);
        const paymentRef = adminDb.collection("payments").doc(orderId);

        await adminDb.runTransaction(async (transaction) => {
          // Idempotency check: prevent duplicate credit on webhook retries
          const paymentDoc = await transaction.get(paymentRef);
          if (paymentDoc.exists) {
            console.log(`[WEBHOOK] Order ${orderId} already credited. Skipping duplicate.`);
            return;
          }

          const userDoc = await transaction.get(userRef);
          if (!userDoc.exists) {
            throw new Error(`User document '${userId}' does not exist`);
          }

          const currentCredit = userDoc.data()?.credit || 0;
          const newCredit = currentCredit + amountInINR;

          transaction.update(userRef, {
            credit: newCredit,
            updatedAt: FieldValue.serverTimestamp(),
          });

          transaction.set(paymentRef, {
            userId,
            orderId,
            amount: amountInINR,
            utr: payload.utr || "",
            status: "SUCCESS",
            timeStamp: FieldValue.serverTimestamp(),
          });

          console.log(`[WEBHOOK] Successfully credited ${amountInINR} points to user '${userId}'`);
        });
      }
    }

    return res.status(200).json({ received: true });
  } catch (error) {
    console.error("[WEBHOOK ERROR]:", error);
    return res.status(500).json({ error: error.message });
  }
}
