import { adminAuth } from "../firebase/admin.js";

/**
 * Verifies the Firebase ID token in `Authorization: Bearer <token>`.
 * Sets req.uid and req.userEmail. The client-supplied userId is never trusted.
 */
export async function requireAuth(req, res, next) {
  const header = req.headers.authorization || "";
  const token = header.startsWith("Bearer ") ? header.slice(7) : "";
  if (!token) {
    return res.status(401).json({ success: false, error: "Unauthorized: missing bearer token" });
  }
  try {
    const decoded = await adminAuth.verifyIdToken(token);
    req.uid = decoded.uid;
    req.userEmail = decoded.email_verified ? (decoded.email || "").toLowerCase() : "";
    return next();
  } catch (e) {
    return res.status(401).json({ success: false, error: "Unauthorized: invalid or expired token" });
  }
}

/** Must run after requireAuth. Fails closed when ADMIN_EMAIL is not configured. */
export function requireAdmin(req, res, next) {
  const adminEmail = (process.env.ADMIN_EMAIL || "").trim().toLowerCase();
  if (!adminEmail || req.userEmail !== adminEmail) {
    return res.status(403).json({ success: false, error: "Forbidden: admin credentials required" });
  }
  return next();
}
