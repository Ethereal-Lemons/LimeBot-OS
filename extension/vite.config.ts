import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

function copyManifestPlugin() {
  return {
    name: "copy-extension-manifest",
    closeBundle() {
      const source = path.resolve(__dirname, "manifest.json");
      const target = path.resolve(__dirname, "dist", "manifest.json");
      fs.copyFileSync(source, target);
    },
  };
}

export default defineConfig({
  plugins: [react(), copyManifestPlugin()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  publicDir: path.resolve(__dirname, "public"),
  build: {
    outDir: "dist",
    emptyOutDir: true,
    rollupOptions: {
      input: {
        popup: path.resolve(__dirname, "popup.html"),
        sidepanel: path.resolve(__dirname, "sidepanel.html"),
        background: path.resolve(__dirname, "src/background.ts"),
      },
      output: {
        entryFileNames(chunkInfo) {
          if (chunkInfo.name === "background") {
            return "background.js";
          }
          return "assets/[name].js";
        },
        chunkFileNames: "assets/[name].js",
        assetFileNames: "assets/[name][extname]",
      },
    },
  },
});
