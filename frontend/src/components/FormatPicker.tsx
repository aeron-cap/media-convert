import { useState } from "preact/hooks";

import type { FormatInfo, Source } from "../api";
import { formatQuality, qualityChoices } from "../format";

const KINDS = [
  { kind: "audio", label: "Audio" },
  { kind: "video", label: "Video" },
] as const;

function qualityLabel(
  format: FormatInfo,
  quality: string,
  source: Source,
  maxHeight: number | null,
): string {
  if (format.kind === "video" && quality === "best") {
    // A TikTok video is usually portrait, where its height reads like a
    // resolution it doesn't have ("1024p"); it has only one anyway.
    return maxHeight && source !== "tiktok" ? `Best available (${maxHeight}p)` : "Best available";
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
 * video quality options to ones the source can actually provide. `source` is
 * the site the video comes from: a TikTok video offers only "best".
 */
export function useFormatChoice(
  formats: FormatInfo[],
  maxHeight: number | null,
  source: Source = "youtube",
): FormatChoice {
  const [formatId, setFormatId] = useState("mp3");
  // The quality picked for each format, so switching formats keeps choices.
  const [picked, setPicked] = useState<Record<string, string>>({});

  const format = formats.find((f) => f.id === formatId) ?? formats[0];
  if (!format) return { format: undefined, quality: null, picker: null };

  const qualities = qualityChoices(format, source, maxHeight);
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
              <span>{qualityLabel(format, q, source, maxHeight)}</span>
            </label>
          ))}
        </fieldset>
      )}

      {format.note && <p class="meta">{format.note}</p>}
    </div>
  );

  return { format, quality, picker };
}
