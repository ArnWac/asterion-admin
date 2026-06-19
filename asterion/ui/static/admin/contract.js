// Cache wrapper around /api/v1/admin/_contract.
// The contract describes every registered resource — we only need to
// fetch it once per page load.

import { admin } from "./api.js";

let _full = null;
const _perResource = new Map();

export async function getFullContract() {
  if (_full) return _full;
  _full = await admin.contract();
  return _full;
}

export async function getResourceContract(resource) {
  if (_perResource.has(resource)) return _perResource.get(resource);
  // Prefer the dedicated endpoint — it tolerates resources that the user
  // is permitted to inspect even when the full contract is huge.
  const meta = await admin.contractFor(resource);
  _perResource.set(resource, meta);
  return meta;
}

export function resetContractCache() {
  _full = null;
  _perResource.clear();
}
