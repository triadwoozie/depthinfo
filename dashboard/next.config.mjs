/** @type {import('next').NextConfig} */
const nextConfig = {
  // Allow MJPEG stream from FastAPI backend in img tags
  images: {
    remotePatterns: [
      {
        protocol: "http",
        hostname: "localhost",
        port: "8000",
      },
    ],
  },
  // Ensure 'use client' components work correctly
  reactStrictMode: true,
}

export default nextConfig
