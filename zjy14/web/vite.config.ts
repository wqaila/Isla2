import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

// 开发时把 /api 与 /health 代理到后端，避免 CORS。
// 后端默认 127.0.0.1:50001（configs/gpu.yaml）。
//
// 用 loadEnv 而不是 process.env，避免依赖 @types/node。
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, ".", "");
  const backend = env.ZJY14_BACKEND || "http://127.0.0.1:50001";
  // 启动器是独立进程（:50100，只用标准库），专门负责「把服务拉起来」。
  const launcher = env.ZJY14_LAUNCHER || "http://127.0.0.1:50100";

  return {
    plugins: [react()],
    server: {
      port: 5173,
      strictPort: true,
      // 显式绑 IPv4：默认 host="localhost" 在本机会解析成 ::1，
      // 导致 `http://127.0.0.1:5173` 连不上（实测），
      // 而启动脚本和文档里都用 127.0.0.1。注意这只绑本机，不对外暴露。
      host: "127.0.0.1",
      proxy: {
        "/api": { target: backend, changeOrigin: true },
        "/health": { target: backend, changeOrigin: true },
        "/synthesize": { target: backend, changeOrigin: true },
        // 启动器路径必须**放在最前**，否则会被上面的 /api 吃掉。
        // 注意 rewrite：页面里的路径是 /launcher/api/xxx，
        // 启动器自身的路由是 /api/xxx。
        "/launcher": {
          target: launcher,
          changeOrigin: true,
          rewrite: (p) => p.replace(/^\/launcher/, ""),
        },
      },
    },
    build: {
      outDir: "dist",
      sourcemap: false,
    },
  };
});
