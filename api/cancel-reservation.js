import { adminDb, FieldValue } from "../firebase/admin.js";

/**
 * Cancels the caller's own unvended (pending) order: refunds the wallet and restores stock.
 * Request: { orderId } with a Bearer ID token.
 */
export async function handleCancelReservation(req, res) {
  try {
    const { orderId } = req.body;
    if (!orderId || typeof orderId !== "string") {
      return res.status(400).json({ success: false, error: "Missing orderId" });
    }

    const result = await adminDb.runTransaction(async (tx) => {
      const orderRef = adminDb.collection("orders").doc(orderId);
      const orderDoc = await tx.get(orderRef);
      if (!orderDoc.exists || orderDoc.data().userId !== req.uid) return "notFound";
      const order = orderDoc.data();
      if (order.status !== "pending") return "notPending";

      const productDocs = await tx.getAll(
        ...order.products.map((p) => adminDb.collection("products").doc(p.productId))
      );
      productDocs.forEach((doc, i) => {
        if (doc.exists) tx.update(doc.ref, { stock: FieldValue.increment(order.products[i].count) });
      });
      tx.update(adminDb.collection("users").doc(req.uid), { credit: FieldValue.increment(order.amount) });
      tx.update(orderRef, { status: "cancelled", updatedAt: FieldValue.serverTimestamp() });
      return "ok";
    });

    if (result === "notFound") return res.status(404).json({ success: false, error: "Order not found" });
    if (result === "notPending") return res.status(409).json({ success: false, error: "Order can no longer be cancelled" });
    return res.status(200).json({ success: true, message: "Order cancelled, credit refunded and stock restored" });
  } catch (e) {
    console.error("[CANCEL-RESERVATION ERROR]:", e);
    return res.status(500).json({ success: false, error: "Internal server error" });
  }
}
