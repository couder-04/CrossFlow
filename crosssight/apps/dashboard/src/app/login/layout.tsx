import { existsSync } from "node:fs";
import { join } from "node:path";
import { Suspense } from "react";

export default function LoginLayout({ children }: { children: React.ReactNode }) {
  const hasBg = existsSync(join(process.cwd(), "public", "login-bg.jpg"));
  return (
    <Suspense fallback={<div className="min-h-screen bg-surface" />}>
      <div className="relative min-h-screen">
        {hasBg && <div className="absolute inset-0 bg-cover bg-center login-bg" />}
        <div className={hasBg ? "absolute inset-0 bg-surface/85 backdrop-blur-sm" : "absolute inset-0 bg-surface"} />
        <div className="relative min-h-screen">{children}</div>
      </div>
    </Suspense>
  );
}
