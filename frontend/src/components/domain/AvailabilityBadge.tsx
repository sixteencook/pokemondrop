import { Badge } from "@/components/ui";
import { AVAILABILITY_META } from "@/lib/format";
import type { Availability } from "@/api/types";

export function AvailabilityBadge({
  availability,
  monitorable,
}: {
  availability: Availability | null;
  monitorable?: boolean;
}) {
  if (!availability) {
    return (
      <Badge tone="neutral" dot>
        {monitorable === false ? "En attente d'URL" : "Jamais vérifié"}
      </Badge>
    );
  }
  const meta = AVAILABILITY_META[availability] ?? AVAILABILITY_META.unknown;
  return (
    <Badge tone={meta.tone} dot pulse={availability === "preorder" || availability === "in_stock"}>
      {meta.label}
    </Badge>
  );
}
