export function normalizePlate(raw: string): string {
  return raw.toUpperCase().replace(/[^A-Z0-9]/g, "");
}

/** Indian plate grammar, after normalization. */
export function isPlate(raw: string): boolean {
  return /^[A-Z]{2}\d{1,2}[A-Z]{1,3}\d{3,4}$/.test(normalizePlate(raw));
}
