// Unit tests for the pure UI logic extracted in stabilization P1.
// These replace the throwaway `node -e` checks used while building the
// Form Layout (5.4) and List View (5.5) features.

import { describe, expect, it } from "vitest";

import {
  allowedDependencyOptions,
  composeDateHierarchy,
  conditionSatisfied,
  editIsDirty,
  groupSidebarModels,
  looseEqual,
  nextSortState,
  parseJsonWidget,
  serializeJsonWidget,
} from "../../asterion/ui/static/admin/logic.js";

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

describe("groupSidebarModels", () => {
  const M = (resource, opts = {}) => ({
    resource,
    label_plural: opts.label || resource,
    category: opts.category ?? null,
    nav_order: opts.nav_order ?? 0,
    show_in_nav: opts.show_in_nav,
  });

  it("keeps uncategorized models flat and ordered by nav_order then label", () => {
    const { ungrouped, groups } = groupSidebarModels(
      [M("b", { label: "Bravo" }), M("a", { label: "Alpha" }), M("z", { label: "Zulu", nav_order: -1 })],
      [],
    );
    expect(groups).toEqual([]);
    expect(ungrouped.map((m) => m.resource)).toEqual(["z", "a", "b"]);
  });

  it("orders groups by the provided categoryOrder; sorts within a group", () => {
    const models = [
      M("orders", { category: "Sales", label: "Orders" }),
      M("items", { category: "Stock", label: "Items", nav_order: 2 }),
      M("bins", { category: "Stock", label: "Bins", nav_order: 1 }),
    ];
    const { groups } = groupSidebarModels(models, ["Stock", "Sales"]);
    expect(groups.map((g) => g.category)).toEqual(["Stock", "Sales"]);
    expect(groups[0].models.map((m) => m.resource)).toEqual(["bins", "items"]);
  });

  it("appends present-but-unlisted categories alphabetically", () => {
    const models = [M("a", { category: "Zeta" }), M("b", { category: "Alpha" })];
    const { groups } = groupSidebarModels(models, []);
    expect(groups.map((g) => g.category)).toEqual(["Alpha", "Zeta"]);
  });

  it("drops show_in_nav === false", () => {
    const { ungrouped } = groupSidebarModels([M("a"), M("hidden", { show_in_nav: false })], []);
    expect(ungrouped.map((m) => m.resource)).toEqual(["a"]);
  });
});

describe("serializeJsonWidget", () => {
  it("pretty-prints a dict/object", () => {
    expect(serializeJsonWidget({ a: 1 })).toBe('{\n  "a": 1\n}');
  });
  it("renders null/empty as empty string", () => {
    expect(serializeJsonWidget(null)).toBe("");
    expect(serializeJsonWidget(undefined)).toBe("");
    expect(serializeJsonWidget("")).toBe("");
  });
  it("passes a string through verbatim", () => {
    expect(serializeJsonWidget('{"x": 1}')).toBe('{"x": 1}');
  });
});

describe("parseJsonWidget", () => {
  it("round-trips a real object", () => {
    expect(parseJsonWidget('{"a": 1, "b": [2]}', false)).toEqual({ a: 1, b: [2] });
  });
  it("empty → null when nullable, {} when not", () => {
    expect(parseJsonWidget("   ", true)).toBe(null);
    expect(parseJsonWidget("", false)).toEqual({});
  });
  it("throws SyntaxError on malformed JSON", () => {
    expect(() => parseJsonWidget("{not json", false)).toThrow(SyntaxError);
  });
});
