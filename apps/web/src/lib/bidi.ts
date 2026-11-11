/**
 * Direction of a transcript line that can mix Urdu (right-to-left) and English (left-to-right).
 *
 * Urdu Sukhan writes Urdu in Urdu script, English words in Latin script and no punctuation, so a line often starts
 * with an English word ("panic attacks ہوتے ہیں"). `dir="auto"` would take the direction of that first word; here the
 * script with more letters decides, and a tie goes to right-to-left (the line is Urdu with English loanwords).
 * The browser's bidi algorithm then lays out the English runs inside an RTL line (and Urdu runs inside an LTR line).
 */
const ARABIC_SCRIPT = /[\u0600-\u06ff\u0750-\u077f\u08a0-\u08ff\ufb50-\ufdff\ufe70-\ufeff]/;
const LATIN = /[A-Za-zÀ-ɏ]/;

export type TextDirection = "rtl" | "ltr";

export function textDirection(text: string | null | undefined): TextDirection {
  let rtl = 0, ltr = 0;
  for (const char of text ?? "") {
    if (ARABIC_SCRIPT.test(char) && /\p{L}/u.test(char)) rtl++;
    else if (LATIN.test(char)) ltr++;
  }
  return rtl > 0 && rtl >= ltr ? "rtl" : "ltr";
}

/** Props for an element showing transcript text: its direction, and `lang="ur"` for Urdu lines (font, line height). */
export function bidiProps(text: string | null | undefined): { dir: TextDirection; lang?: string } {
  const dir = textDirection(text);
  return dir === "rtl" ? { dir, lang: "ur" } : { dir };
}
