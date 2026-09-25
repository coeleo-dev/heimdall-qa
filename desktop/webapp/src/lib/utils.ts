import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

/** shadcn's one utility: conditional classes with Tailwind conflicts resolved last-wins. */
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

/** Pretty-print anything the API sends, for the read-only JSON editors. */
export function prettyJson(value: unknown): string {
  if (value === null || value === undefined || value === "") return "";
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

/** `unknown` as a display string, without turning `null` into `"null"`. */
export function text(value: unknown): string {
  if (value === null || value === undefined) return "";
  if (typeof value === "object") return prettyJson(value);
  return String(value);
}
