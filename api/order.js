import { nanoid } from "nanoid";

const YOUPI_GATEWAY_URL = (
  process.env.YOUPI_GATEWAY_URL ||
  (process.env.YOUPI_PORT ? `http://localhost:${process.env.YOUPI_PORT}` : "http://localhost:5000")
).replace(/\/$/, "");

const MERCHANT_ID = process.env.YOUPI_MERCHANT_ID || "00000000-0000-0000-0000-000000000001";

/**
 * Initiates dynamic UPI payment with YouPI-PG Gateway.
 */
export async function handleCreatePaymentSession(req, res) {
  const userId = req.uid; // verified by requireAuth
  const { amount, currency = "INR", returnUrl } = req.body;

  if (!amount || Number(amount) <= 0) {
    return res.status(400).json({ error: "Invalid recharge amount" });
  }

  const orderId = `WAL-${nanoid(8)}`;

  try {
    const gatewayRes = await fetch(`${YOUPI_GATEWAY_URL}/api/v1/payments`, {
      method: "POST",
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
      checkoutUrl: data.checkoutUrl,
    });
  } catch (error) {
    console.warn("[PAYMENT GATEWAY NOTICE] Gateway unreachable, providing offline/mock fallback:", error.message);
    return res.status(200).json({
      success: true,
      orderId,
      checkoutUrl: `/checkout/${orderId}?amount=${amount}`,
      note: "Offline/mock gateway fallback",
    });
  }
}
