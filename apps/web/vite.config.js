import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { readFileSync } from "node:fs";
export default defineConfig({
    plugins: [react()],
    server: {
        fs: { deny: [".env", ".env.*", "**/.git/**", "**/*.{pem,key}", "**/data/**"] },
        https: process.env.VITE_HTTPS_CERT && process.env.VITE_HTTPS_KEY ? {
            cert: readFileSync(process.env.VITE_HTTPS_CERT),
            key: readFileSync(process.env.VITE_HTTPS_KEY),
        } : undefined,
        proxy: {
            "/api": {
                target: process.env.VITE_API_TARGET || "http://127.0.0.1:8000",
                ws: true,
                configure: function (proxy) {
                    // Never let a LAN guest impersonate the local clinician via a header.
                    proxy.on("proxyReq", function (proxyReq, req) {
                        proxyReq.setHeader("X-Pshynox-Client-IP", req.socket.remoteAddress || "unknown");
                    });
                },
            },
        },
    },
});
