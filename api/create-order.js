import { adminDb, FieldValue } from "../firebase/admin.js";
import { nanoid } from "nanoid";

class OrderError extends Error {
  constructor(code, message) {
    super(message || code);
    this.code = code;
  }
}

/**
 * Creates a paid order in one atomic transaction:
 *   verify stock -> reserve stock -> deduct wallet -> write pending order.
 * Price, slot and name come from the `products` collection, never from the client.
 * Request: { products: [{ productId, count }] } with a Bearer ID token.
 */
export async function handleCreateOrder(req, res) {
  try {
    const userId = req.uid;
    const { products } = req.body;

    if (!Array.isArray(products) || products.length === 0) {
      return res.status(400).json({ success: false, error: "Missing or invalid products list" });
    }

    // Merge duplicate productIds and validate counts
    const wanted = new Map();
    for (const p of products) {
      const count = Number(p?.count ?? 1);
      if (!p?.productId || !Number.isInteger(count) || count < 1 || count > 10) {
        return res.status(400).json({ success: false, error: "Invalid product entry" });
      }
      wanted.set(p.productId, (wanted.get(p.productId) || 0) + count);
    }

    const orderId = nanoid(10);

    await adminDb.runTransaction(async (tx) => {
      const userRef = adminDb.collection("users").doc(userId);
      const productRefs = [...wanted.keys()].map((id) => adminDb.collection("products").doc(id));
      const [userDoc, ...productDocs] = await tx.getAll(userRef, ...productRefs);

      if (!userDoc.exists) throw new OrderError("noUser", "User does not exist");

      const items = [];
      let total = 0;
      for (const doc of productDocs) {
        if (!doc.exists) throw new OrderError("insufficientStock");
        const data = doc.data();
        const count = wanted.get(doc.id);
        if ((data.stock ?? 0) < count) throw new OrderError("insufficientStock");
        const price = Number(data.price);
        if (!data.position || !Number.isFinite(price) || price < 0) {
          throw new OrderError("badProduct", `Product '${doc.id}' is misconfigured`);
        }
        total += price * count;
        items.push({
          ref: doc.ref,
          stock: data.stock,
          item: {
            productId: doc.id,
            name: data.name || doc.id,
            position: String(data.position).toUpperCase(),
            price,
            count,
          },
        });
      }

      const credit = userDoc.data()?.credit ?? 0;
      if (credit < total) throw new OrderError("insufficientCredit");

      for (const { ref, stock, item } of items) {
        tx.update(ref, { stock: stock - item.count });
      }
      tx.update(userRef, { credit: credit - total });
      tx.set(adminDb.collection("orders").doc(orderId), {
        userId,
        products: items.map((i) => i.item),
        amount: total,
        status: "pending",
        timeStamp: FieldValue.serverTimestamp(),
      });
    });

    return res.status(200).json({ success: true, orderId, message: "Order created successfully" });
  } catch (e) {
    if (e instanceof OrderError) {
      if (e.code === "insufficientCredit") {
        return res.status(200).json({ success: false, insufficientCredit: true, error: "Insufficient wallet credits" });
      }
      if (e.code === "insufficientStock") {
        return res.status(200).json({ success: false, insufficientStock: true, error: "Product is out of stock" });
      }
      return res.status(400).json({ success: false, error: e.message });
    }
    console.error("[CREATE-ORDER ERROR]:", e);
    return res.status(500).json({ success: false, error: "Internal server error" });
  }
}
