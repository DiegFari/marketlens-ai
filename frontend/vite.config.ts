import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// In development the API runs separately (`make run-api`); the dev server proxies to it.
// In production Caddy serves the built files and routes /api to the API container.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": "http://localhost:8000",
      "/health": "http://localhost:8000",
    },
  },
  test: {
    environment: "node",
  },
});
