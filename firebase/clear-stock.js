import { adminDb } from "./admin.js";

/**
 * Empties the shelves: sets stock = 0 on every document in `products`.
 * The storefront lists only products with stock > 0, so they disappear from the shop.
 * Nothing is deleted; refill with `npm run seed` or by editing the stock field.
 *
 *   node firebase/clear-stock.js           dry run: lists what would change
 *   node firebase/clear-stock.js --apply   writes the changes
 *   node firebase/clear-stock.js --apply --delete   deletes the product documents instead
 */
const apply = process.argv.includes("--apply");
const remove = process.argv.includes("--delete");

const snapshot = await adminDb.collection("products").get();
if (snapshot.empty) {
  console.log("No products found.");
  process.exit(0);
}

console.log(`Project: ${adminDb.projectId ?? "(from credentials)"}`);
for (const doc of snapshot.docs) {
  const d = doc.data();
  console.log(`  [${d.position ?? "?"}] ${d.name ?? doc.id}  stock ${d.stock ?? "?"} -> ${remove ? "DELETE" : 0}`);
}

if (!apply) {
  console.log(`\nDry run: ${snapshot.size} product(s) would change. Re-run with --apply to write.`);
  process.exit(0);
}

// Firestore batches hold at most 500 writes
for (let i = 0; i < snapshot.docs.length; i += 400) {
  const batch = adminDb.batch();
  for (const doc of snapshot.docs.slice(i, i + 400)) {
    if (remove) batch.delete(doc.ref);
    else batch.update(doc.ref, { stock: 0 });
  }
  await batch.commit();
}
console.log(`\nDone: ${snapshot.size} product(s) ${remove ? "deleted" : "set to stock 0"}.`);
process.exit(0);
