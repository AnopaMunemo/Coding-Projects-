import type { NextConfig } from "next";

const config: NextConfig = {
  output: "standalone",          // small runtime image; see Dockerfile
  reactStrictMode: true,
  poweredByHeader: false,
  experimental: {
    // The portal reads client personal information. Never cache a page render
    // across requests — a stale render is a cross-tenant leak.
    staleTimes: { dynamic: 0, static: 0 },
  },
};

export default config;
