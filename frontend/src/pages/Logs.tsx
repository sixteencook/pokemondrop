/** Logs en direct : historique initial via l'API + nouvelles lignes via
 *  WebSocket. Filtres par niveau, recherche, pause. */

import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Pause, Play, ScrollText } from "lucide-react";
import { logsApi } from "@/api/endpoints";
import type { LogEntry } from "@/api/types";
import { Badge, Button, Card, EmptyState, Input, PageHeader, Select } from "@/components/ui";
import { LOG_LEVEL_TONE } from "@/lib/format";
import { useWsEvent } from "@/ws/WsProvider";

const LEVELS = ["", "INFO", "CHECK", "WARN", "ALERTE", "ERROR"];
const MAX_LINES = 500;

export default function LogsPage() {
  const [level, setLevel] = useState("");
  const [search, setSearch] = useState("");
  const [paused, setPaused] = useState(false);
  const [lines, setLines] = useState<LogEntry[]>([]);
  const counter = useRef(1_000_000); // ids locaux pour les lignes WebSocket
  const scrollRef = useRef<HTMLDivElement>(null);

  const initial = useQuery({
    queryKey: ["logs", "initial"],
    queryFn: () => logsApi.list({ page_size: 200 }),
  });

  useEffect(() => {
    if (initial.data) {
      setLines([...initial.data.items].reverse()); // ordre chronologique
    }
  }, [initial.data]);

  useWsEvent("log", (message) => {
    if (paused) return;
    const payload = message.payload as Omit<LogEntry, "id">;
    setLines((current) =>
      [...current, { ...payload, id: ++counter.current }].slice(-MAX_LINES)
    );
  });

  useEffect(() => {
    if (!paused) {
      scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
    }
  }, [lines, paused]);

  const visible = lines.filter((line) => {
    if (level && line.level !== level) return false;
    if (search) {
      const needle = search.toLowerCase();
      return (
        line.message.toLowerCase().includes(needle) ||
        line.logger.toLowerCase().includes(needle)
      );
    }
    return true;
  });

  return (
    <>
      <PageHeader
        title="Logs"
        description="Flux en direct via WebSocket — l'historique durable reste dans logs/."
        actions={
          <Button
            variant={paused ? "primary" : "secondary"}
            size="sm"
            icon={paused ? <Play size={13} /> : <Pause size={13} />}
            onClick={() => setPaused((current) => !current)}
          >
            {paused ? "Reprendre" : "Pause"}
          </Button>
        }
      />

      <Card padded={false}>
        <div className="flex flex-wrap gap-2 border-b border-border p-3">
          <Select className="h-8 w-32 text-xs" value={level}
                  onChange={(event) => setLevel(event.target.value)}>
            {LEVELS.map((value) => (
              <option key={value} value={value}>{value || "Tous les niveaux"}</option>
            ))}
          </Select>
          <Input className="h-8 w-64 text-xs" placeholder="Rechercher…"
                 value={search} onChange={(event) => setSearch(event.target.value)} />
        </div>

        <div ref={scrollRef} className="h-[60vh] overflow-y-auto p-3 font-mono text-xs">
          {visible.length === 0 ? (
            <EmptyState icon={<ScrollText size={24} />} title="Aucune ligne"
                        description="Les logs apparaîtront ici en direct." />
          ) : (
            visible.map((line) => (
              <div key={line.id} className="flex gap-2 rounded px-1 py-0.5 hover:bg-surface-2">
                <span className="shrink-0 text-faint">{line.time}</span>
                <span className="w-14 shrink-0">
                  <Badge tone={LOG_LEVEL_TONE[line.level] ?? "neutral"}>{line.level}</Badge>
                </span>
                <span className="shrink-0 text-faint">{line.logger}</span>
                <span className="break-all text-text/90">{line.message}</span>
              </div>
            ))
          )}
        </div>
      </Card>
    </>
  );
}
