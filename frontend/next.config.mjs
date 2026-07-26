/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,

  /**
   * Proxy all /api/* requests to the FastAPI backend during development.
   * POST /api/generate is handled by src/app/api/generate/route.ts with a
   * long timeout — do not rely on the rewrite alone for generation.
   */
  async rewrites() {
    const backendUrl = process.env.BACKEND_URL ?? "http://localhost:8000";
    return [
      {
        source: "/api/:path*",
        destination: `${backendUrl}/:path*`,
      },
    ];
  },
};

export default nextConfig;
