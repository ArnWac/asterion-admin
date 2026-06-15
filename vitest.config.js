import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    // Only our own JS unit tests; the Python suite lives under tests/ too.
    include: ["tests/js/**/*.test.js"],
    environment: "node",
  },
});
