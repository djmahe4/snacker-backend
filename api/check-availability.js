import { adminDb } from "../firebase/admin.js";

/**
 * Atomic stock check & wallet credit deduction.
 * Ensures users have sufficient points and slots have sufficient stock before order generation.
 */
export async function handleCheckAvailability(req, res) {
  try {
    const { userId, products, amount } = req.body;

    if (!userId) {
      return res.status(401).json({ success: false, error: "Unauthorized: Missing userId" });
    }
    if (!products || !Array.isArray(products) || products.length === 0) {
      return res.status(400).json({ success: false, error: "Invalid products array" });
    }

    await adminDb.runTransaction(async (transaction) => {
      const userRef = adminDb.collection("users").doc(userId);
      const userDoc = await transaction.get(userRef);

      if (!userDoc.exists) {
        throw new Error("User does not exist");
      }

      const userCredits = userDoc.data()?.credit ?? 0;
      if (userCredits < amount) {
        throw new Error("insufficientCredit");
      }

      // Retrieve product documents in parallel
      const productRefs = products.map((product) =>
        adminDb.collection("products").doc(product.productId)
      );
      const productDocs = await transaction.getAll(...productRefs);

      // Verify stock availability and prepare updates
      for (const productDoc of productDocs) {
        if (!productDoc.exists) {
          throw new Error("insufficientStock");
        }
        const product = products.find((p) => p.productId === productDoc.id);
        const productData = productDoc.data();
        const currentStock = productData?.stock ?? 0;
        const newStock = currentStock - (product?.count ?? 1);

        if (newStock < 0) {
          throw new Error("insufficientStock");
        }

        transaction.update(productDoc.ref, { stock: newStock });
      }

      // Deduct credits from user
      const newCredits = userCredits - amount;
      transaction.update(userRef, { credit: newCredits });
    });

    return res.status(200).json({
      success: true,
      message: "Purchase Successful: Stock reserved and credits deducted",
    });
  } catch (e) {
    console.error("[CHECK-AVAILABILITY ERROR]:", e.message);

    if (e.message === "insufficientCredit") {
      return res.status(200).json({ success: false, insufficientCredit: true, error: "Insufficient wallet credits" });
    } else if (e.message === "insufficientStock") {
      return res.status(200).json({
        success: false,
        insufficientCredit: false,
        insufficientStock: true,
        error: "Product is out of stock",
      });
    }

    return res.status(500).json({
      success: false,
      insufficientCredit: false,
      insufficientStock: false,
      error: e.message,
    });
  }
}
