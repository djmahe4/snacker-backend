import { adminDb } from "../firebase/admin.js";

/**
 * Read-only pre-check (no side effects). The actual reservation, wallet deduction
 * and order creation happen atomically in /api/create-order.
 */
export async function handleCheckAvailability(req, res) {
  try {
    const { products } = req.body;
    if (!Array.isArray(products) || products.length === 0) {
      return res.status(400).json({ success: false, error: "Invalid products array" });
    }

    const userDoc = await adminDb.collection("users").doc(req.uid).get();
    if (!userDoc.exists) {
      return res.status(404).json({ success: false, error: "User does not exist" });
    }

    const docs = await adminDb.getAll(
      ...products.map((p) => adminDb.collection("products").doc(String(p.productId)))
    );
    let total = 0;
    for (let i = 0; i < docs.length; i++) {
      const count = Number(products[i].count ?? 1);
      const data = docs[i].data();
      if (!docs[i].exists || !Number.isInteger(count) || count < 1 || (data.stock ?? 0) < count) {
        return res.status(200).json({ success: false, insufficientCredit: false, insufficientStock: true, error: "Product is out of stock" });
      }
      total += Number(data.price) * count;
    }
    if ((userDoc.data()?.credit ?? 0) < total) {
      return res.status(200).json({ success: false, insufficientCredit: true, error: "Insufficient wallet credits" });
    }
    return res.status(200).json({ success: true, amount: total, message: "Available" });
  } catch (e) {
    console.error("[CHECK-AVAILABILITY ERROR]:", e);
    return res.status(500).json({ success: false, error: "Internal server error" });
  }
}
