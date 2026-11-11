import { test } from "node:test";
import assert from "node:assert/strict";
import { bidiProps, textDirection } from "../src/lib/bidi.ts";

test("Urdu lines are right-to-left even when they start with an English word", () => {
  assert.equal(textDirection("مجھے آج کل بہت anxiety ہو رہی ہے"), "rtl");
  assert.equal(textDirection("panic attacks آتے ہیں جب میں office جاتا ہوں"), "rtl");
  assert.equal(textDirection("نیند"), "rtl");
});

test("English lines, with or without an Urdu word, stay left-to-right", () => {
  assert.equal(textDirection("I have been sleeping badly for two weeks"), "ltr");
  assert.equal(textDirection("I keep thinking about the exam بس"), "ltr");
  assert.equal(textDirection(""), "ltr");
  assert.equal(textDirection("12 34"), "ltr");
});

test("Urdu lines get lang=ur for the Urdu font; English ones do not", () => {
  assert.deepEqual(bidiProps("ٹھیک ہے"), { dir: "rtl", lang: "ur" });
  assert.deepEqual(bidiProps("Okay"), { dir: "ltr" });
});
