const ISIN_RE = /^[A-Z]{2}[A-Z0-9]{9}\d$/;

export function isValidIsinFormat(value: string): boolean {
  return ISIN_RE.test(value);
}
