import assert from "node:assert/strict";
import test from "node:test";
import { duration } from "./duration.ts";

test("rounds the total before splitting hours and minutes", () => {
  assert.equal(duration(59.6), "1h");
  assert.equal(duration(119.6), "2h");
  assert.equal(duration(45), "45 min");
  assert.equal(duration(125), "2h 05m");
  assert.equal(duration(0), "5 min");
  assert.equal(duration(55, 15), "1h");
  assert.equal(duration(100, 15), "1h 45m");
});
