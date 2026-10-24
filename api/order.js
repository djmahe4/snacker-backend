import { nanoid } from "nanoid";
import { adminDb, FieldValue } from "../firebase/admin.js";

const YOUPI_GATEWAY_URL = (
  process.env.YOUPI_GATEWAY_URL ||
  (process.env.YOUPI_PORT ? `http://localhost:${process.env.YOUPI_PORT}` : "http://localhost:5000")
).replace(/\/$/, "");

const MERCHANT_ID = process.env.YOUPI_MERCHANT_ID || "00000000-0000-0000-0000-000000000001";

// YouPI-PG in localhost mode always builds checkout links on http://localhost:7000, which a
// phone cannot open. Swap the origin for the public gateway URL (nginx routes /checkout/).
function toPublicCheckoutUrl(checkoutUrl, gatewayUrl) {
  if (!checkoutUrl || !/^https?:\/\//.test(gatewayUrl)) return checkoutUrl;
  try {
    const local = ["localhost", "127.0.0.1", "0.0.0.0"];
    const link = new URL(checkoutUrl);
    if (!local.includes(link.hostname)) return checkoutUrl;
    const publicBase = new URL(gatewayUrl);
    if (local.includes(publicBase.hostname)) return checkoutUrl;
    return `${publicBase.origin}${link.pathname}${link.search}`;
  } catch {
    return checkoutUrl;
  }
}

/**
 * Initiates dynamic UPI payment with YouPI-PG Gateway.
 */
export async function handleCreatePaymentSession(req, res) {
  const userId = req.uid; // verified by requireAuth
  const { amount, currency = "INR", returnUrl } = req.body;

  if (!amount || Number(amount) <= 0) {
    return res.status(400).json({ error: "Invalid recharge amount" });
  }

  // The webhook credits users/{userId}; make sure it exists before any money moves
  try {
    await adminDb.collection("users").doc(userId).create({
      name: "Student",
      userId,
      email: req.userEmail || "",
      credit: 0,
      createdAt: FieldValue.serverTimestamp(),
    });
  } catch (e) {
    if (e?.code !== 6) {
      console.error("[ORDER] Could not ensure user document:", e.message);
      return res.status(500).json({ error: "Could not load your wallet. Please try again." });
    }
  }

  const orderId = `WAL-${nanoid(8)}`;

  try {
    const gatewayRes = await fetch(`${YOUPI_GATEWAY_URL}/api/v1/payments`, {
      method: "POST",
      signal: AbortSignal.timeout(8000),
      headers: {
        "Content-Type": "application/json",
        "X-Merchant-Id": MERCHANT_ID,
      },
      body: JSON.stringify({
        orderId,
        amount: parseFloat(amount).toFixed(2),
        currency,
        returnUrl: returnUrl || "/",
        metadata: { userId },
      }),
    });

    const data = await gatewayRes.json();
    if (!gatewayRes.ok) {
      return res.status(gatewayRes.status).json({
        error: data.error || "Payment gateway error",
      });
    }

    return res.status(200).json({
      success: true,
      orderId: data.orderId || orderId,
      checkoutUrl: toPublicCheckoutUrl(data.checkoutUrl, YOUPI_GATEWAY_URL),
    });
  } catch (error) {
    console.error("YouPI-PG gateway unreachable:", YOUPI_GATEWAY_URL, error.message);
    return res.status(503).json({
      error: "Payment gateway is offline. Please try again in a minute.",
    });
  }
}
