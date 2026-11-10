import { useState } from "preact/hooks";

import type { FormatInfo } from "../api";
import { formatQuality } from "../format";

const KINDS = [
  { kind: "audio", label: "Audio" },
  { kind: "video", label: "Video" },
] as const;

/** The qualities of `format` that make sense for a video of this height. */
function qualitiesFor(format: FormatInfo, maxHeight: number | null): string[] {
  if (format.kind !== "video" || maxHeight == null) return format.qualities;
  return format.qualities.filter((q) => q === "best" || Number(q) <= maxHeight);
}

function qualityLabel(format: FormatInfo, quality: string, maxHeight: number | null): string {
  if (format.kind === "video" && quality === "best") {
    return maxHeight ? `Best available (${maxHeight}p)` : "Best available";
  }
  return formatQuality(format.kind, quality);
}

export interface FormatChoice {
  /** The chosen format; undefined only until the format list has loaded. */
  format: FormatInfo | undefined;
  /** The chosen quality, or null when the format has no quality choice. */
  quality: string | null;
  /** The format and quality controls, to place in the form. */
  picker: preact.JSX.Element | null;
}

/**
 * State and controls for choosing an output format and quality.
 * `maxHeight` is the height of the source video when known; it trims the
 * video quality options to ones the source can actually provide.
 */
export function useFormatChoice(formats: FormatInfo[], maxHeight: number | null): FormatChoice {
  const [formatId, setFormatId] = useState("mp3");
  // The quality picked for each format, so switching formats keeps choices.
  const [picked, setPicked] = useState<Record<string, string>>({});

  const format = formats.find((f) => f.id === formatId) ?? formats[0];
  if (!format) return { format: undefined, quality: null, picker: null };

  const qualities = qualitiesFor(format, maxHeight);
  const quality =
    qualities.length === 0
      ? null
      : qualities.includes(picked[format.id])
        ? picked[format.id]
        : qualities.includes(format.default_quality ?? "")
          ? format.default_quality
          : qualities[0];

  const picker = (
    <div class="options">
      {KINDS.map(({ kind, label }) => (
        <fieldset class="formats" key={kind}>
          <legend>{label}</legend>
          {formats
            .filter((option) => option.kind === kind)
            .map((option) => (
              <label class="format" key={option.id}>
                <input
                  type="radio"
                  name="format"
                  value={option.id}
                  checked={format.id === option.id}
                  onChange={() => setFormatId(option.id)}
                />
                <span>{option.label}</span>
              </label>
            ))}
        </fieldset>
      ))}

      {quality !== null && (
        <fieldset class="formats">
          <legend>Quality</legend>
          {qualities.map((q) => (
            <label class="format" key={q}>
              <input
                type="radio"
                name="quality"
                value={q}
                checked={quality === q}
                onChange={() => setPicked({ ...picked, [format.id]: q })}
              />
              <span>{qualityLabel(format, q, maxHeight)}</span>
            </label>
          ))}
        </fieldset>
      )}

      {format.note && <p class="meta">{format.note}</p>}
    </div>
  );

  return { format, quality, picker };
}
