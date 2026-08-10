import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "standalone",
  // Dev-only (rewrites are dev-only too): the default 30s proxy timeout
  // 500s any long API call — dojo Analyse runs ~40-90s (an Opus pass +
  // verification extraction), so give the proxy real headroom.
  experimental: {
    proxyTimeout: 300_000,
  },
  async rewrites() {
    // In production, the load balancer / nginx handles API routing.
    // The rewrite is only needed for local development.
    if (process.env.NODE_ENV === "production") {
      return [];
    }
    return [
      {
        source: "/api/:path*",
        destination: "http://localhost:8000/api/:path*",
      },
    ];
  },
};

export default nextConfig;
