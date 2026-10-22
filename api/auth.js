import { createClerkClient, verifyToken } from "@clerk/backend";

const clerk = createClerkClient({ secretKey: process.env.CLERK_SECRET_KEY });

/**
 * Verifies the Clerk session token in `Authorization: Bearer <token>`
 * (from `getToken()` on the frontend). Sets req.uid to the Clerk user id and
 * req.userEmail to the user's verified primary email. The client-supplied userId is never trusted.
 */
export async function requireAuth(req, res, next) {
  const header = req.headers.authorization || "";
  const token = header.startsWith("Bearer ") ? header.slice(7) : "";
  if (!token) {
    return res.status(401).json({ success: false, error: "Unauthorized: missing bearer token" });
  }
  try {
    const claims = await verifyToken(token, { secretKey: process.env.CLERK_SECRET_KEY });
    req.uid = claims.sub;
    req.userEmail = "";
    try {
      const user = await clerk.users.getUser(claims.sub);
      const primary = user.emailAddresses.find((e) => e.id === user.primaryEmailAddressId);
      if (primary?.verification?.status === "verified") {
        req.userEmail = primary.emailAddress.toLowerCase();
      }
    } catch (e) {
      console.error("[AUTH] Failed to load Clerk user:", e.message);
    }
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
