"""Building a zip archive on the fly, without writing it to disk first."""

import zipfile
from pathlib import Path
from typing import Iterator

CHUNK = 1024 * 1024


class _Sink:
    """A write-only, non-seekable file that hands back what was written to it."""

    def __init__(self) -> None:
        self._chunks: list[bytes] = []
        self._position = 0

    def write(self, data: bytes) -> int:
        self._chunks.append(bytes(data))
        self._position += len(data)
        return len(data)

    def tell(self) -> int:
        return self._position

    def flush(self) -> None:
        pass

    def take(self) -> bytes:
        data = b"".join(self._chunks)
        self._chunks.clear()
        return data


def zip_stream(files: list[tuple[str, Path]]) -> Iterator[bytes]:
    """Yield a zip archive of `files` (name in the archive, path on disk).

    Files are stored without compression: media is already compressed.
    """
    sink = _Sink()
    with zipfile.ZipFile(sink, "w", zipfile.ZIP_STORED, allowZip64=True) as archive:
        for name, path in files:
            entry = zipfile.ZipInfo.from_file(path, name)
            entry.compress_type = zipfile.ZIP_STORED
            with archive.open(entry, "w", force_zip64=True) as target, path.open("rb") as source:
                while chunk := source.read(CHUNK):
                    target.write(chunk)
                    yield sink.take()
    yield sink.take()


def unique_names(names: list[str]) -> list[str]:
    """Make archive names distinct by adding " (2)", " (3)"… before the extension."""
    seen: dict[str, int] = {}
    result = []
    for name in names:
        key = name.lower()
        seen[key] = seen.get(key, 0) + 1
        if seen[key] > 1:
            stem, dot, extension = name.rpartition(".")
            name = f"{stem} ({seen[key]}){dot}{extension}" if dot else f"{name} ({seen[key]})"
        result.append(name)
    return result
