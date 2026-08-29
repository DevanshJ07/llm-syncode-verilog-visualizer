/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,

  /**
   * Proxy /api/* to FastAPI during development.
   *
   * Long-running or large-body paths must NOT rely on rewrites alone:
   * Next's rewrite proxy defaults to ~30s and returns HTTP 500 on timeout
   * while the backend may still complete successfully.
   *
   * Dedicated App Router handlers (take precedence over rewrites):
   *   - src/app/api/generate/route.ts
   *   - src/app/api/generate/jobs/route.ts
   *   - src/app/api/generate/jobs/[jobId]/route.ts
   *   - src/app/api/experiment/[id]/route.ts
   *   - src/app/api/import/bundle/route.ts
   *   - src/app/api/imported-experiment/[id]/route.ts
   *
   * BACKEND_URL is environment-configured (default http://127.0.0.1:8000).
   */
  async rewrites() {
    const backendUrl = process.env.BACKEND_URL ?? "http://127.0.0.1:8000";
    return [
      {
        source: "/api/:path*",
        destination: `${backendUrl}/:path*`,
      },
    ];
  },
};

export default nextConfig;
