import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

// 开发时把 /api 与 /health 代理到后端，避免 CORS。
// 后端默认 127.0.0.1:50001（configs/gpu.yaml）。
//
// 用 loadEnv 而不是 process.env，避免依赖 @types/node。
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, ".", "");
  const backend = env.ZJY14_BACKEND || "http://127.0.0.1:50001";

  return {
    plugins: [react()],
    server: {
      port: 5173,
      strictPort: true,
      proxy: {
        "/api": { target: backend, changeOrigin: true },
        "/health": { target: backend, changeOrigin: true },
        "/synthesize": { target: backend, changeOrigin: true },
      },
    },
    build: {
      outDir: "dist",
      sourcemap: false,
    },
  };
});
