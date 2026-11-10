import type { FormatInfo } from "../api";
import { formatQuality } from "../format";

/** A compact menu for changing the quality of waiting or finished conversions. */
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
      title="Change quality"
      value={value ?? ""}
      onChange={(event) => {
        const chosen = event.currentTarget.value;
        // Show the current quality until the change is confirmed and saved;
        // the new one arrives through `value` when it is.
        event.currentTarget.value = value ?? "";
        onChange(chosen);
      }}
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
