import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// The build lands in the Python package, which serves it (dissolve web). `npm run dev` proxies the API to it.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  build: { outDir: "../src/dissolve/ui", emptyOutDir: true },
  server: { proxy: { "/api": "http://127.0.0.1:8765" } },
});
