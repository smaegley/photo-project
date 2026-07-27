import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev server runs on the VM; the Mac browser hits it at 10.0.1.121:5173.
// /api and /health are proxied to the FastAPI backend so everything is
// same-origin (no CORS, and image URLs like /api/thumbnails/... just work).
export default defineConfig({
  plugins: [react()],
  server: {
    host: "0.0.0.0",
    port: 5173,
    // The VM shares its inotify budget with VS Code Server, which watches the whole
    // workspace. When that is exhausted Vite dies at startup with
    // `ENOSPC: System limit for number of file watchers reached` — a host limit, not a
    // code fault, and not fixable from here without root. Polling costs a little CPU
    // and needs no watchers at all, which makes dev startup independent of whatever
    // else happens to be running.
    watch: {
      usePolling: true,
      interval: 400,
      ignored: ["**/node_modules/**", "**/.git/**", "**/dist/**"],
    },
    proxy: {
      "/api": "http://127.0.0.1:8077",
      "/health": "http://127.0.0.1:8077",
    },
  },
});
