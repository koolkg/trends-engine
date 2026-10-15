import { CATEGORIES, type Category } from "../types";
import { categoryLabel } from "../format";

interface Props {
  active: Category | null;
  onChange: (c: Category | null) => void;
}

export function CategoryChips({ active, onChange }: Props) {
  const chip = (value: Category | null, label: string) => (
    <button
      key={label}
      type="button"
      className={`chip${active === value ? " active" : ""}`}
      aria-pressed={active === value}
      onClick={() => onChange(value)}
    >
      {label}
    </button>
  );
  return (
    <div className="chips" role="group" aria-label="Filter by category">
      {chip(null, "All")}
      {CATEGORIES.map((c) => chip(c, categoryLabel(c)))}
    </div>
  );
}
