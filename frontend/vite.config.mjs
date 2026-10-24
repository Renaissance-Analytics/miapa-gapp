import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { fileURLToPath } from "node:url";
import { createBuildInfo } from "./tools/build-info.mjs";

// Local development transport only. No backend proxy is silently guessed.
const backend = process.env.MAIPA_BACKEND_ORIGIN;
if (backend && !/^http:\/\/127\.0\.0\.1:\d+$/.test(backend)) {
  throw new Error("MAIPA_BACKEND_ORIGIN must be an explicit loopback development origin");
}
export default defineConfig(({ command }) => {
  const build = createBuildInfo(fileURLToPath(new URL("../", import.meta.url)));
  return {
  define: { __MAIPA_BUILD_ID__: JSON.stringify(build.build_id), __MAIPA_RELEASE_LABEL__: JSON.stringify(build.release_label), __MAIPA_UPDATES_ENABLED__: JSON.stringify(command === "build") },
  plugins: [react(), tailwindcss(), {
    name: "maipa-build-identity",
    generateBundle() { this.emitFile({ type: "asset", fileName: "build-info.json", source: JSON.stringify(build) }); },
  }],
  server: {
    host: "127.0.0.1",
    strictPort: true,
    allowedHosts: ["maipa.gen"],
    proxy: backend ? { "/api": { target: backend, changeOrigin: true } } : undefined,
  },
};
});
