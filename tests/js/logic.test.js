// Unit tests for the pure UI logic extracted in stabilization P1.
// These replace the throwaway `node -e` checks used while building the
// Form Layout (5.4) and List View (5.5) features.

import { describe, expect, it } from "vitest";

import {
  allowedDependencyOptions,
  composeDateHierarchy,
  conditionSatisfied,
  editIsDirty,
  looseEqual,
  nextSortState,
} from "../../adminfoundry/ui/static/admin/logic.js";

describe("looseEqual", () => {
  it("treats number/string forms as equal", () => {
    expect(looseEqual(2, "2")).toBe(true);
    expect(looseEqual("2", 2)).toBe(true);
  });
  it("keeps booleans and exact matches", () => {
    expect(looseEqual(true, true)).toBe(true);
    expect(looseEqual("x", "x")).toBe(true);
  });
  it("is false for null vs value and genuine mismatches", () => {
    expect(looseEqual(null, "x")).toBe(false);
    expect(looseEqual("a", "b")).toBe(false);
  });
});

describe("conditionSatisfied", () => {
  it("no condition → always visible", () => {
    expect(conditionSatisfied(null, "anything")).toBe(true);
  });
  it("equals rule", () => {
    expect(conditionSatisfied({ field: "x", equals: "published" }, "published")).toBe(true);
    expect(conditionSatisfied({ field: "x", equals: "published" }, "draft")).toBe(false);
    expect(conditionSatisfied({ field: "x", equals: true }, true)).toBe(true);
    expect(conditionSatisfied({ field: "x", equals: 2 }, "2")).toBe(true);
  });
  it("in rule", () => {
    expect(conditionSatisfied({ field: "x", in: ["a", "b"] }, "b")).toBe(true);
    expect(conditionSatisfied({ field: "x", in: ["a", "b"] }, "c")).toBe(false);
  });
  it("null controlling value does not satisfy a concrete rule", () => {
    expect(conditionSatisfied({ field: "x", equals: "y" }, null)).toBe(false);
  });
});

describe("allowedDependencyOptions", () => {
  const dep = { field: "country", options: { US: ["CA", "NY"], DE: ["BY"] } };
  it("returns the mapped options for a known value", () => {
    expect(allowedDependencyOptions(dep, "US")).toEqual(["CA", "NY"]);
    expect(allowedDependencyOptions(dep, "DE")).toEqual(["BY"]);
  });
  it("returns [] for unknown / null controlling value or missing dependency", () => {
    expect(allowedDependencyOptions(dep, "FR")).toEqual([]);
    expect(allowedDependencyOptions(dep, null)).toEqual([]);
    expect(allowedDependencyOptions(null, "US")).toEqual([]);
  });
});

describe("nextSortState", () => {
  it("cycles ascending → descending → off", () => {
    expect(nextSortState(null, "title")).toBe("title");
    expect(nextSortState("title", "title")).toBe("-title");
    expect(nextSortState("-title", "title")).toBe(null);
  });
  it("switching to a different column starts ascending", () => {
    expect(nextSortState("name", "title")).toBe("title");
    expect(nextSortState("-name", "title")).toBe("title");
  });
});

describe("composeDateHierarchy", () => {
  it("builds progressively from the filled levels", () => {
    expect(composeDateHierarchy("2026", "", "")).toBe("2026");
    expect(composeDateHierarchy("2026", "3", "")).toBe("2026-03");
    expect(composeDateHierarchy("2026", "3", "5")).toBe("2026-03-05");
  });
  it("returns null without a year and ignores a day without a month", () => {
    expect(composeDateHierarchy("", "", "")).toBe(null);
    expect(composeDateHierarchy("2026", "", "5")).toBe("2026");
  });
});

describe("editIsDirty", () => {
  it("detects changes and treats reverts / equal forms as clean", () => {
    expect(editIsDirty("New", "Old")).toBe(true);
    expect(editIsDirty("Old", "Old")).toBe(false);
    expect(editIsDirty(5, "5")).toBe(false);
    expect(editIsDirty("", null)).toBe(false);
    expect(editIsDirty(true, false)).toBe(true);
  });
});
