import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  async rewrites() {
    // Next does not serve a directory index from public/, so map the clean URL.
    return [{ source: "/loopline", destination: "/loopline/index.html" }];
  },
};

export default nextConfig;
