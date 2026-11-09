// Patient-only build for the public meeting server: `npm run build:meeting` -> dist-meeting/.
// Only meeting.html, its bundle, the audio worklet and the logo are emitted (no public/ copy, no source maps).
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { readFileSync } from "node:fs";

export default defineConfig({
  plugins: [react(), {
    name: "meeting-static-files",
    generateBundle() {
      this.emitFile({ type: "asset", fileName: "meeting-pcm.js", source: readFileSync("public/meeting-pcm.js", "utf8") });
      this.emitFile({ type: "asset", fileName: "images/logo.png", source: readFileSync("public/images/logo.png") });
    },
  }],
  publicDir: false,
  build: {
    outDir: "dist-meeting",
    emptyOutDir: true,
    sourcemap: false,
    assetsInlineLimit: 0,
    rollupOptions: { input: "meeting.html" },
  },
});
