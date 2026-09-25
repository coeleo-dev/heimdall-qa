/// <reference types="vitest/config" />
import { fileURLToPath, URL } from "node:url";

import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// The client is built here and *shipped from the Python package*. `outDir` points at
// `src/heimdall_qa/desktop/webapp` rather than `dist/` for two reasons that matter:
// the wheel's `package-data` can only reach inside the package, and `pip install` must
// never require Node. The bundle is committed, and a CI job rebuilds it and fails on
// drift — the same contract a lockfile has.
//
// `base` is relative because the app is served from a path the shell chooses, not from
// the root of a domain, and absolute asset URLs would 404 there.
export default defineConfig({
  base: "./",
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) },
  },
  build: {
    outDir: fileURLToPath(new URL("../../src/heimdall_qa/desktop/webapp", import.meta.url)),
    emptyOutDir: true,
    assetsDir: "assets",
    // Monaco is large on purpose and the warning would be noise. The wheel grew by it
    // and `contrib/architecture.md` §9.2 declares that cost rather than hiding it.
    chunkSizeWarningLimit: 4096,
    rollupOptions: {
      output: {
        // Monaco in its own chunk: it is the biggest dependency and the one that
        // changes least, so keeping it apart keeps the app chunk's diffs readable.
        manualChunks: { monaco: ["monaco-editor"] },
      },
    },
  },
  server: {
    // The dev server is for working on the client; the API it talks to is the running
    // harness, reached through the proxy below so the token header has one origin.
    port: 5179,
    proxy: { "/api": "http://127.0.0.1:8731" },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
  },
});
