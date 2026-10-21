import { adminDb } from "../firebase/admin.js";

/**
 * Cancels a reservation and restores snack inventory stock.
 */
export async function handleCancelReservation(req, res) {
  try {
    const { products } = req.body;

    if (!products || !Array.isArray(products) || products.length === 0) {
      return res.status(400).json({ success: false, error: "Invalid products array" });
    }

    await adminDb.runTransaction(async (transaction) => {
      const productRefs = products.map((p) =>
        adminDb.collection("products").doc(p.productId)
      );
      const productDocs = await transaction.getAll(...productRefs);

      for (const doc of productDocs) {
        if (!doc.exists) continue;
        const item = products.find((p) => p.productId === doc.id);
        const currentStock = doc.data()?.stock || 0;
        const restoredStock = currentStock + (item?.count || 1);
        transaction.update(doc.ref, { stock: restoredStock });
      }
    });

    return res.status(200).json({ success: true, message: "Reservation cancelled and stock restored" });
  } catch (error) {
    console.error("[CANCEL-RESERVATION ERROR]:", error);
    return res.status(500).json({ success: false, error: error.message });
  }
}
