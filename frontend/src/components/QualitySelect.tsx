import type { FormatInfo } from "../api";
import { formatQuality } from "../format";

/** A compact menu for changing the quality of conversions that haven't started. */
export function QualitySelect({
  format,
  value,
  label,
  onChange,
}: {
  format: FormatInfo;
  value: string | null;
  /** Read by screen readers; says whose quality this changes. */
  label: string;
  onChange: (quality: string) => void;
}) {
  return (
    <select
      class="quality-select"
      aria-label={label}
      title="Change quality before it starts"
      value={value ?? ""}
      onChange={(event) => onChange(event.currentTarget.value)}
    >
      {value == null && (
        <option value="" disabled>
          Mixed
        </option>
      )}
      {format.qualities.map((quality) => (
        <option value={quality} key={quality}>
          {formatQuality(format.kind, quality)}
        </option>
      ))}
    </select>
  );
}
