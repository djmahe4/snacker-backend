import { adminDb, FieldValue } from "../firebase/admin.js";
import { nanoid } from "nanoid";

/**
 * Creates an authorized pending order in Firestore.
 * Generates unique QR-compatible nanoid for physical vending.
 */
export async function handleCreateOrder(req, res) {
  try {
    const { userId, products, amount } = req.body;

    if (!userId) {
      return res.status(401).json({ success: false, error: "Unauthorized: Missing userId" });
    }
    if (!products || !Array.isArray(products) || products.length === 0) {
      return res.status(400).json({ success: false, error: "Missing or invalid products list" });
    }

    const orderId = nanoid(10); // Standard 10-char nanoid

    await adminDb.collection("orders").doc(orderId).set({
      userId: userId,
      products: products,
      amount: Number(amount) || 0,
      status: "pending",
      timeStamp: FieldValue.serverTimestamp(),
    });

    return res.status(200).json({
      success: true,
      orderId: orderId,
      message: "Order created successfully",
    });
  } catch (error) {
    console.error("[CREATE-ORDER ERROR]:", error);
    return res.status(500).json({
      success: false,
      error: error.message,
    });
  }
}
