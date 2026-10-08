import { AlertTriangle } from "lucide-react";
import { alertTypeIcons } from "@/components/ui/icons";
import { cn } from "@/lib/utils";

export function AlertTypeIcon({ type, className }: { type: string; className?: string }) {
  const Icon = alertTypeIcons[type] ?? AlertTriangle;
  return <Icon size={14} className={cn("shrink-0", className)} aria-hidden />;
}
