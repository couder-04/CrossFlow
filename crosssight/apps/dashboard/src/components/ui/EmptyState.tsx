"use client";

import { motion } from "framer-motion";
import type { LucideIcon } from "lucide-react";

export function EmptyState({
  icon: Icon,
  title,
  description,
  indicator = false,
}: {
  icon: LucideIcon;
  title: string;
  description?: string;
  indicator?: boolean;
}) {
  return (
    <div className="flex flex-col items-start gap-2 py-2 text-muted">
      <Icon size={16} aria-hidden />
      <p className="text-xs text-slate-200">{title}</p>
      {description && <p className="text-xs">{description}</p>}
      {indicator && (
        <span className="flex gap-1" aria-hidden>
          {[0, 1, 2].map((index) => (
            <motion.span
              key={index}
              className="h-1.5 w-1.5 rounded-full bg-accent"
              animate={{ opacity: [0.25, 1, 0.25] }}
              transition={{ duration: 1.2, repeat: Infinity, delay: index * 0.2 }}
            />
          ))}
        </span>
      )}
    </div>
  );
}
