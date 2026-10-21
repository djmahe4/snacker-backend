import express from "express";
import cors from "cors";
import dotenv from "dotenv";
import { handleCheckAvailability } from "./check-availability.js";
import { handleCreateOrder } from "./create-order.js";
import { handleYoupiWebhook } from "./youpi-webhook.js";
import { handleCreatePaymentSession } from "./order.js";
import { handleUserSync } from "./auth-sync.js";
import { handleAdminActions } from "./admin-actions.js";
import { handleCancelReservation } from "./cancel-reservation.js";

dotenv.config();

const app = express();
const PORT = process.env.PORT || 5000;

// Middleware
app.use(cors({ origin: true, credentials: true }));

// Capture raw body for webhook HMAC validation while parsing JSON for all endpoints
app.use(
  express.json({
    verify: (req, res, buf) => {
      req.rawBody = buf.toString("utf8");
    },
  })
);

app.use(express.urlencoded({ extended: true }));

// Health Check
app.get("/health", (req, res) => {
  res.status(200).json({
    status: "ok",
    service: "snacker-backend",
    timestamp: new Date().toISOString(),
    uptime: process.uptime(),
  });
});

// API Routes
app.post("/api/check-availability", handleCheckAvailability);
app.post("/api/create-order", handleCreateOrder);
app.post("/api/youpi-webhook", handleYoupiWebhook);
app.post("/api/order", handleCreatePaymentSession);
app.post("/api/auth-sync", handleUserSync);
app.post("/api/admin/actions", handleAdminActions);
app.post("/api/cancel-reservation", handleCancelReservation);

// 404 Handler
app.use((req, res) => {
  res.status(404).json({ error: `Route not found: ${req.method} ${req.originalUrl}` });
});

// Global Error Handler
app.use((err, req, res, next) => {
  console.error("[SERVER UNCAUGHT ERROR]:", err);
  res.status(500).json({ error: "Internal server error", message: err.message });
});

app.listen(PORT, "0.0.0.0", () => {
  console.log(`=======================================================`);
  console.log(`  🍫 SNACKER BACKEND SERVER RUNNING ON PORT ${PORT}    `);
  console.log(`  Health Check: http://localhost:${PORT}/health        `);
  console.log(`=======================================================`);
});

export default app;
