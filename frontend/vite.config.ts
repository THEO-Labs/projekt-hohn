import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";
import path from "path";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const proxyTarget = env.VITE_API_PROXY_TARGET || "http://localhost:8000";
  const isRemoteHttps = proxyTarget.startsWith("https:");

  return {
    plugins: [react()],
    resolve: {
      alias: { "@": path.resolve(__dirname, "./src") },
    },
    server: {
      proxy: {
        "/api": {
          target: proxyTarget,
          changeOrigin: true,
          secure: false,
          cookieDomainRewrite: "",
          configure: (proxy) => {
            proxy.on("proxyRes", (proxyRes) => {
              if (!isRemoteHttps) return;
              const sc = proxyRes.headers["set-cookie"];
              if (!sc) return;
              proxyRes.headers["set-cookie"] = sc.map((c) =>
                c.replace(/;\s*Secure/gi, "").replace(/;\s*Domain=[^;]+/gi, ""),
              );
            });
          },
        },
      },
    },
  };
});
