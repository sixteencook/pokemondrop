import { ChevronLeft, ChevronRight } from "lucide-react";
import { Button } from "./Button";

interface PaginationProps {
  page: number;
  pages: number;
  total: number;
  onChange: (page: number) => void;
}

export function Pagination({ page, pages, total, onChange }: PaginationProps) {
  if (pages <= 1) {
    return <p className="px-1 py-2 text-xs text-faint">{total} élément(s)</p>;
  }
  return (
    <div className="flex items-center justify-between px-1 py-2">
      <p className="text-xs text-faint">
        Page {page} / {pages} — {total} élément(s)
      </p>
      <div className="flex gap-1.5">
        <Button size="sm" variant="ghost" disabled={page <= 1}
                onClick={() => onChange(page - 1)} icon={<ChevronLeft size={14} />}>
          Précédent
        </Button>
        <Button size="sm" variant="ghost" disabled={page >= pages}
                onClick={() => onChange(page + 1)}>
          Suivant <ChevronRight size={14} />
        </Button>
      </div>
    </div>
  );
}
