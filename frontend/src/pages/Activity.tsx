/** Flux d'activité global (timeline de tous les produits). */

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Activity } from "lucide-react";
import { timelineApi } from "@/api/endpoints";
import {
  Card,
  EmptyState,
  PageHeader,
  Pagination,
  Spinner,
  TimelineList,
} from "@/components/ui";
import { EVENT_TYPE_META, formatDateTime } from "@/lib/format";

export default function ActivityPage() {
  const [page, setPage] = useState(1);
  const timeline = useQuery({
    queryKey: ["timeline", "global", page],
    queryFn: () => timelineApi.list({ page, page_size: 30 }),
  });

  return (
    <>
      <PageHeader
        title="Activité"
        description="La timeline complète : chaque événement de la vie de vos produits."
      />
      <Card>
        {timeline.isLoading && <Spinner />}
        {timeline.data && timeline.data.items.length === 0 && (
          <EmptyState
            icon={<Activity size={24} />}
            title="Aucun événement"
            description="Le flux se remplira dès que la surveillance sera active."
          />
        )}
        {timeline.data && timeline.data.items.length > 0 && (
          <>
            <TimelineList
              items={timeline.data.items.map((entry) => ({
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
            <Pagination page={timeline.data.page} pages={timeline.data.pages}
                        total={timeline.data.total} onChange={setPage} />
          </>
        )}
      </Card>
    </>
  );
}
