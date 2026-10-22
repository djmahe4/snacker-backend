import { getApps, initializeApp, cert } from "firebase-admin/app";
import { getFirestore, FieldValue } from "firebase-admin/firestore";
import { getDatabase } from "firebase-admin/database";
import { getAuth } from "firebase-admin/auth";
import fs from "fs";
import path from "path";
import dotenv from "dotenv";

dotenv.config();

function loadServiceAccount() {
  // 1. From environment variable (raw JSON string or base64)
  if (process.env.FIREBASE_SERVICE_ACCOUNT_KEY) {
    try {
      const raw = process.env.FIREBASE_SERVICE_ACCOUNT_KEY.trim();
      const parsed = JSON.parse(
        raw.startsWith("{") ? raw : Buffer.from(raw, "base64").toString("utf-8")
      );
      if (parsed.private_key) {
        parsed.private_key = parsed.private_key.replace(/\\n/g, "\n");
      }
      return parsed;
    } catch (e) {
      console.warn("Failed to parse FIREBASE_SERVICE_ACCOUNT_KEY from env:", e.message);
    }
  }

  // 2. From file path in env or local files
  const searchPaths = [
    process.env.FIREBASE_SERVICE_ACCOUNT_PATH,
    path.join(process.cwd(), "serviceAccountKey.json"),
    path.join(process.cwd(), "firebase", "serviceAccountKey.json"),
    path.join(process.cwd(), "..", "serviceAccountKey.json"),
    "/home/pi/snacker/serviceAccountKey.json",
  ].filter(Boolean);

  for (const filePath of searchPaths) {
    if (fs.existsSync(filePath)) {
      try {
        const fileContent = fs.readFileSync(filePath, "utf-8");
        const parsed = JSON.parse(fileContent);
        if (parsed.private_key) {
          parsed.private_key = parsed.private_key.replace(/\\n/g, "\n");
        }
        return parsed;
      } catch (e) {
        console.warn(`Failed to parse ${filePath}:`, e.message);
      }
    }
  }

  return null;
}

const serviceAccount = loadServiceAccount();

let adminApp;
if (getApps().length > 0) {
  adminApp = getApps()[0];
} else if (serviceAccount && serviceAccount.project_id) {
  adminApp = initializeApp({
    credential: cert(serviceAccount),
    projectId: serviceAccount.project_id,
    databaseURL: process.env.FIREBASE_DATABASE_URL || process.env.NEXT_PUBLIC_FIREBASE_DATABASE_URL,
  });
} else {
  adminApp = initializeApp({
    projectId: process.env.FIREBASE_PROJECT_ID || process.env.NEXT_PUBLIC_FIREBASE_PROJECT_ID || "snacker-app",
    databaseURL: process.env.FIREBASE_DATABASE_URL || process.env.NEXT_PUBLIC_FIREBASE_DATABASE_URL,
  });
}

export const adminDb = getFirestore(adminApp);
export const adminRtdb = getDatabase(adminApp);
export const adminAuth = getAuth(adminApp);
export { FieldValue };
export default adminApp;
