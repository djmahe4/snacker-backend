import { adminDb, adminRtdb, FieldValue } from "../firebase/admin.js";
import { DEFAULT_SNACKS } from "../firebase/seed.js";

const ADMIN_EMAIL = process.env.ADMIN_EMAIL || "example@gmail.com";

/**
 * Handles admin inventory operations, restocking, catalog seeding, and maintenance toggle.
 */
export async function handleAdminActions(req, res) {
  try {
    const { action, payload, requesterEmail } = req.body;

    // Strict admin authorization check
    if (requesterEmail?.toLowerCase() !== ADMIN_EMAIL.toLowerCase()) {
      return res.status(403).json({ success: false, error: "Forbidden: Admin credentials required" });
    }

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

        await adminDb.collection("products").doc(productId).update({
          stock: Number(newStock),
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
