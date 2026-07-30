/** Timeline d'un produit (modal « Historique »). */

import { useQuery } from "@tanstack/react-query";
import type { Product } from "@/api/types";
import { productsApi } from "@/api/endpoints";
import { EmptyState, Modal, Spinner, TimelineList } from "@/components/ui";
import { EVENT_TYPE_META, formatDateTime } from "@/lib/format";
import { History } from "lucide-react";

interface ProductTimelineModalProps {
  product: Product | null;
  onClose: () => void;
}

export function ProductTimelineModal({ product, onClose }: ProductTimelineModalProps) {
  const { data, isLoading } = useQuery({
    queryKey: ["timeline", "product", product?.uuid],
    queryFn: () => productsApi.timeline(product!.uuid, { page_size: 100 }),
    enabled: product !== null,
  });

  return (
    <Modal
      open={product !== null}
      title={product ? `Historique — ${product.name}` : "Historique"}
      onClose={onClose}
    >
      {isLoading && <Spinner />}
      {data && data.items.length === 0 && (
        <EmptyState
          icon={<History size={24} />}
          title="Aucun événement"
          description="La timeline se remplira dès que la surveillance démarrera."
        />
      )}
      {data && data.items.length > 0 && (
        <TimelineList
          items={data.items.map((entry) => ({
            id: entry.id,
            label: entry.label,
            time: formatDateTime(entry.created_at),
            tone: EVENT_TYPE_META[entry.event_type]?.tone ?? "neutral",
            meta:
              entry.old_value || entry.new_value
                ? `${entry.old_value ?? "—"} → ${entry.new_value ?? "—"}` +
                  (entry.price ? ` · ${entry.price}` : "")
                : entry.price ?? undefined,
          }))}
        />
      )}
    </Modal>
  );
}
