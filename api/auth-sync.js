import { adminDb, FieldValue } from "../firebase/admin.js";

/**
 * Creates or synchronizes a user profile in Firestore.
 * Initializes the student's prepaid points wallet to 0 without overwriting existing balance.
 */
export async function handleUserSync(req, res) {
  try {
    const { userId, name, email } = req.body;

    if (!userId) {
      return res.status(400).json({ error: "Missing required field: userId" });
    }

    const userRef = adminDb.collection("users").doc(userId);
    const userDoc = await userRef.get();

    const displayName = name || email?.split("@")[0] || "Student";

    if (!userDoc.exists) {
      // New user registration: initialize with 0 points
      await userRef.set({
        name: displayName,
        userId: userId,
        email: email || "",
        credit: 0,
        createdAt: FieldValue.serverTimestamp(),
      });
      console.log(`[USER SYNC] Created new user record for '${userId}' (${displayName})`);
    } else {
      // Existing user: preserve wallet balance and update metadata
      await userRef.set(
        {
          name: displayName,
          email: email || userDoc.data().email || "",
          updatedAt: FieldValue.serverTimestamp(),
        },
        { merge: true }
      );
      console.log(`[USER SYNC] Updated user profile for '${userId}'`);
    }

    return res.status(200).json({ success: true, message: "User synced successfully" });
  } catch (error) {
    console.error("[USER SYNC ERROR]:", error);
    return res.status(500).json({ error: error.message });
  }
}
