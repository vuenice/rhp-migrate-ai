// Fallback to local 127.0.0.1:8000 if BACKEND_API_URL is unset or uses docker hostname 'api' outside container
const rawBackendUrl = process.env.BACKEND_API_URL || "http://127.0.0.1:8000";
const isDocker = Boolean(process.env.DOCKER || process.env.IS_DOCKER);
const backendApiUrl =
  rawBackendUrl.includes("://api:") && !isDocker
    ? "http://127.0.0.1:8000"
    : rawBackendUrl;

/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  output: "standalone",
  // Allow proxying /api requests to FastAPI backend on port 8000 during development
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: `${backendApiUrl}/api/:path*`,
      },
    ];
  },
};

export default nextConfig;
