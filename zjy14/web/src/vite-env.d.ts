/// <reference types="vite/client" />

/** Vite 环境变量类型声明。
 *
 * 为什么需要这个文件：
 *   `import.meta.env` 的类型由 `vite/client` 提供。
 *   不引入时 tsc 会报 `Property 'env' does not exist on type 'ImportMeta'`。
 *
 * 注意：本项目没装 `@types/node`（刻意的，见 vite.config.ts 注释），
 * 所以这里只声明用到的变量，不引入 Node 类型。
 */

interface ImportMetaEnv {
  /** zjy13 监控端点基址，默认 http://127.0.0.1:8765 */
  readonly VITE_ZJY13_BASE?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
