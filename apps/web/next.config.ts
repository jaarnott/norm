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
        // API_PROXY_TARGET lets a second checkout's dev server (a worktree)
        // talk to its own API instead of the main checkout's.
        destination: `${process.env.API_PROXY_TARGET || "http://localhost:8000"}/api/:path*`,
      },
    ];
  },
  // A Norm link sent as /?page=… (the closing manager's email builder did
  // this until Oct 2026) opened the marketing home page. Send it to the app,
  // which keeps the query and translates old page ids (pageAliases.ts).
  async redirects() {
    return [
      { source: "/", has: [{ type: "query", key: "page" }], destination: "/app", permanent: false },
    ];
  },
  async headers() {
    // Norm Functions' customer pages. /q (quote) and /e (event portal) carry a
    // secret link in the path: no referrer may leak it, search engines must
    // not keep it, and no other site may frame the page (a framed Confirm
    // button is a clickjacking target). /f (web form) exists to be framed by
    // venue websites, so framing stays open there. The embeds load on venue
    // sites: short cache so a fix reaches them within minutes.
    const noIndex = { key: "X-Robots-Tag", value: "noindex, nofollow" };
    const noReferrer = { key: "Referrer-Policy", value: "no-referrer" };
    const unframed = [
      noIndex,
      noReferrer,
      { key: "X-Frame-Options", value: "DENY" },
      { key: "Content-Security-Policy", value: "frame-ancestors 'none'" },
    ];
    return [
      { source: "/q/:path*", headers: unframed },
      { source: "/e/:path*", headers: unframed },
      // The Entertainers App: the performer portal and the onboarding form
      // (bank and tax details) — never indexed, never framed, no referrer.
      { source: "/p/:path*", headers: unframed },
      { source: "/join/:path*", headers: unframed },
      {
        source: "/f/:path*",
        headers: [noIndex, noReferrer, { key: "Content-Security-Policy", value: "frame-ancestors *" }],
      },
      {
        source: "/embed/:file*",
        headers: [
          { key: "Cache-Control", value: "public, max-age=300, stale-while-revalidate=86400" },
          { key: "Cross-Origin-Resource-Policy", value: "cross-origin" },
          { key: "X-Content-Type-Options", value: "nosniff" },
        ],
      },
    ];
  },
};

export default nextConfig;
