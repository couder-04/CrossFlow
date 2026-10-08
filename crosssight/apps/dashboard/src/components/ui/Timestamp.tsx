import { formatTsWithZone } from "@/lib/utils";

export function Timestamp({ iso, className }: { iso: string; className?: string }) {
  const { primary, secondary } = formatTsWithZone(iso);
  return (
    <time dateTime={iso} title={secondary} className={className}>
      {primary}
    </time>
  );
}
