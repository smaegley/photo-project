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
    proxy: {
      "/api": "http://127.0.0.1:8077",
      "/health": "http://127.0.0.1:8077",
    },
  },
});
