#!/usr/bin/env python3
"""
Scanner Simulation Helper
Tests direct injection of an orderId into Firestore to simulate a QR scan.
"""

import sys
import os
import time

try:
    import firebase_admin
    from firebase_admin import credentials, firestore
except ImportError:
    print("Please install firebase-admin: pip install firebase-admin")
    sys.exit(1)

FIREBASE_KEY_PATH = os.environ.get(
    "FIREBASE_SERVICE_ACCOUNT_PATH",
    os.path.join(os.path.dirname(__file__), "..", "serviceAccountKey.json")
)

def main():
    if len(sys.argv) < 2:
        print("Usage: python test_scanner_simulator.py <order_id> [slot=A1] [user_id=test_user]")
        print("Example: python test_scanner_simulator.py ord_12345678 A1 user_abc")
        sys.exit(1)

    order_id = sys.argv[1]
    slot = sys.argv[2] if len(sys.argv) > 2 else "A1"
    user_id = sys.argv[3] if len(sys.argv) > 3 else "test_user_01"

    if os.path.exists(FIREBASE_KEY_PATH):
        cred = credentials.Certificate(FIREBASE_KEY_PATH)
        firebase_admin.initialize_app(cred)
    else:
        firebase_admin.initialize_app()

    db = firestore.client()

    print(f"Creating simulated pending order '{order_id}' in Firestore...")
    db.collection("orders").document(order_id).set({
        "userId": user_id,
        "amount": 20,
        "status": "pending",
        "products": [
            {
                "productId": "lays-classic",
                "name": "Classic Salted Lays",
                "position": slot,
                "count": 1,
            }
        ],
        "timeStamp": firestore.SERVER_TIMESTAMP,
    })

    print(f"✅ Order '{order_id}' created with status 'pending' on slot '{slot}'.")
    print(f"Now, if vending_controller.py is running, scan '{order_id}' or trigger the event loop!")

if __name__ == "__main__":
    main()
