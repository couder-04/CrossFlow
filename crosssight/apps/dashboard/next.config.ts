import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import type { NextConfig } from "next";

const version = (
  JSON.parse(readFileSync(join(dirname(fileURLToPath(import.meta.url)), "package.json"), "utf8")) as {
    version: string;
  }
).version;

function publicAppEnv(): string {
  const explicit = process.env.NEXT_PUBLIC_APP_ENV ?? process.env.APP_ENV;
  if (explicit === "prod" || explicit === "production") return "prod";
  if (explicit === "dev" || explicit === "development") return "dev";
  return process.env.NODE_ENV === "production" ? "prod" : "dev";
}

const nextConfig: NextConfig = {
  output: "standalone",
  reactStrictMode: true,
  transpilePackages: ["maplibre-gl"],
  env: {
    NEXT_PUBLIC_VERSION: version,
    NEXT_PUBLIC_APP_ENV: publicAppEnv(),
  },
  // Middleware verifies HS256 with process.env.JWT_SECRET at request time.
  // That value must be the same secret the API signs with. It is not copied
  // into `env`, which would inline the secret into the browser bundle.
};

export default nextConfig;
