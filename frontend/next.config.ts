import type { NextConfig } from "next";

const backendUrl = (process.env.BACKEND_URL ?? "").trim().replace(/\/+$/, "");

if (!backendUrl) {
  throw new Error("BACKEND_URL is not set. Add it to frontend/.env.");
}

const nextConfig: NextConfig = {
  output: "standalone",
  cacheComponents: true,
  partialPrefetching: true,
  experimental: {
    proxyTimeout: 240_000,
  },
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
  async rewrites() {
    return [
      {
        source: "/api/backend/:path*",
        destination: `${backendUrl}/:path*`,
      },
    ];
  },
};

export default nextConfig;
