import { adminDb, adminRtdb, FieldValue } from "../firebase/admin.js";
import { DEFAULT_SNACKS } from "../firebase/seed.js";

/**
 * Handles admin inventory operations, restocking, catalog seeding, and maintenance toggle.
 */
export async function handleAdminActions(req, res) {
  try {
    // Authorization is enforced by the requireAuth + requireAdmin middleware (verified Clerk session token).
    const { action, payload } = req.body;

    switch (action) {
      case "get_catalog": {
        const snapshot = await adminDb.collection("products").get();
        const products = [];
        snapshot.forEach((doc) => products.push({ id: doc.id, ...doc.data() }));

        let maintenance = false;
        try {
          if (adminRtdb) {
            const snap = await adminRtdb.ref("/onStockUpdation/status").get();
            if (snap.exists()) maintenance = Boolean(snap.val());
          }
        } catch (e) {
          console.warn("[ADMIN] RTDB read skipped:", e.message);
        }

        return res.status(200).json({ success: true, products, maintenance });
      }

      case "update_stock": {
        const { productId, newStock } = payload;
        if (!productId || newStock === undefined) {
          return res.status(400).json({ success: false, error: "Missing productId or newStock" });
        }
        // NaN or negative stock would defeat the `stock < count` check in /api/create-order
        const safeStock = Number(newStock);
        if (!Number.isInteger(safeStock) || safeStock < 0) {
          return res.status(400).json({ success: false, error: "newStock must be a non-negative integer" });
        }

        await adminDb.collection("products").doc(productId).update({
          stock: safeStock,
          updatedAt: FieldValue.serverTimestamp(),
        });
        return res.status(200).json({ success: true, message: `Stock updated for ${productId}` });
      }

      case "toggle_maintenance": {
        const { status } = payload;
        if (adminRtdb) {
          await adminRtdb.ref("/onStockUpdation/status").set(Boolean(status));
        }
        return res.status(200).json({ success: true, maintenance: Boolean(status) });
      }

      case "seed_defaults": {
        for (const item of DEFAULT_SNACKS) {
          await adminDb.collection("products").doc(item.id).set({
            ...item,
            updatedAt: FieldValue.serverTimestamp(),
          }, { merge: true });
        }
        return res.status(200).json({ success: true, message: "Catalog seeded successfully" });
      }

      default:
        return res.status(400).json({ success: false, error: `Unknown action '${action}'` });
    }
  } catch (error) {
    console.error("[ADMIN ERROR]:", error);
    return res.status(500).json({ success: false, error: error.message });
  }
}
