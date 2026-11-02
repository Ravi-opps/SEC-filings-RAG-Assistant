import type { NextConfig } from "next";

const configuredBackendUrl = (process.env.BACKEND_URL ?? "")
  .trim()
  .replace(/\/+$/, "");

if (!configuredBackendUrl) {
  throw new Error(
    "BACKEND_URL is not set. Add it to frontend/.env or to the Vercel environment variables.",
  );
}

if (!/^https?:\/\//i.test(configuredBackendUrl)) {
  throw new Error(
    `BACKEND_URL must start with http:// or https:// (received "${configuredBackendUrl}").`,
  );
}

if (
  process.env.VERCEL &&
  /^https?:\/\/(localhost|127\.0\.0\.1)(:\d+)?$/i.test(configuredBackendUrl)
) {
  throw new Error(
    "BACKEND_URL points to localhost, which is unreachable from Vercel. Set it to the public backend origin instead.",
  );
}

const backendUrl = configuredBackendUrl;

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
