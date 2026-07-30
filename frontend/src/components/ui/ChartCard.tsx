import type { ReactNode } from "react";
import { ResponsiveContainer } from "recharts";
import { Card } from "./Card";
import { EmptyState } from "./EmptyState";
import { BarChart3 } from "lucide-react";

interface ChartCardProps {
  title: string;
  height?: number;
  isEmpty?: boolean;
  emptyLabel?: string;
  children: ReactNode; // un chart Recharts
}

/** Couleurs partagées par tous les graphiques (tokens du thème). */
export const CHART_COLORS = {
  accent: "#5e6ad2",
  success: "#22c55e",
  warning: "#f59e0b",
  danger: "#ef4444",
  info: "#38bdf8",
  grid: "#26262b",
  text: "#8b8b93",
};

export function ChartCard({
  title,
  height = 220,
  isEmpty = false,
  emptyLabel = "Pas encore de données",
  children,
}: ChartCardProps) {
  return (
    <Card title={title} padded={false}>
      {isEmpty ? (
        <EmptyState icon={<BarChart3 size={24} />} title={emptyLabel} />
      ) : (
        <div className="p-3" style={{ height }}>
          <ResponsiveContainer width="100%" height="100%">
            {children as never}
          </ResponsiveContainer>
        </div>
      )}
    </Card>
  );
}
