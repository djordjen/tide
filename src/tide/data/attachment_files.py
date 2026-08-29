"""The files themselves, on a filesystem TIDE was pointed at.

Bytes are not in any database. A managed SQLite file is what the verified
backup copies and checksums, and a legacy database is not TIDE's to widen;
a filesystem is the one store that serves both, and it is what an operator
already knows how to move, mirror and snapshot.

The layout is derived, not recorded: `<root>/<first two characters of the
guid>/<guid>`. 256 directories keep any one of them from becoming the whole
store, and deriving the shard from the key means there is no counter to
coordinate between processes and nothing extra to keep -- given a key, a
recovery tool knows the path.

Two properties the service above depends on. A stored file carries no
extension, because the row is the authority on what the bytes are and an
extensionless tree cannot be served or executed by a web server pointed at
it by mistake. And bytes become visible under their key by a rename out of
`tmp/`, so a file is either whole or absent: an interrupted or refused
write leaves nothing a sweep has to reason about.
"""

from __future__ import annotations

from collections.abc import Iterable
import os
from pathlib import Path
from typing import BinaryIO

from tide.services.attachment_store import AttachmentStoreError, measured

STAGING = "tmp"
SHARD_CHARACTERS = 2


class FilesystemAttachmentBytes:
    """Attachment bytes under a configured root."""

    def __init__(self, root: str | os.PathLike[str]) -> None:
        self.root = Path(root)
        if self.root.exists() and not self.root.is_dir():
            raise AttachmentStoreError(
                f"attachment root {self.root} is not a directory"
            )
        self._staging = self.root / STAGING
        try:
            self._staging.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            raise AttachmentStoreError(
                f"attachment root {self.root} could not be prepared"
            ) from error

    def write(
        self, guid: str, chunks: Iterable[bytes], *, limit: int
    ) -> tuple[int, str]:
        staged = self._staging / guid
        size = 0
        digest = ""
        try:
            # Exclusive: a key that already has a staged write is a collision
            # rather than a retry, and overwriting one would be replacing
            # somebody's file with somebody else's.
            with open(staged, "xb") as handle:
                for chunk, size, digest in measured(chunks, limit):
                    handle.write(chunk)
                handle.flush()
                os.fsync(handle.fileno())
        except FileExistsError as error:
            raise AttachmentStoreError(
                f"attachment {guid} is already being written"
            ) from error
        except BaseException:
            # Every failure leaves the staging directory as it was found:
            # too large, a disconnected upload, a full disk, an interrupt.
            staged.unlink(missing_ok=True)
            raise
        destination = self._path(guid)
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists():
                raise AttachmentStoreError(f"attachment {guid} already has bytes")
            os.replace(staged, destination)
        except BaseException:
            staged.unlink(missing_ok=True)
            raise
        return size, digest

    def open(self, guid: str) -> BinaryIO:
        try:
            return open(self._path(guid), "rb")
        except OSError as error:
            raise AttachmentStoreError(f"attachment {guid} has no bytes") from error

    def delete(self, guid: str) -> None:
        # Forgiving by design: a sweep runs after crashes, where a row and
        # its bytes disagreeing about what still exists is ordinary.
        try:
            self._path(guid).unlink(missing_ok=True)
        except OSError as error:
            raise AttachmentStoreError(
                f"attachment {guid} could not be removed"
            ) from error

    def exists(self, guid: str) -> bool:
        return self._path(guid).is_file()

    def all_guids(self) -> tuple[str, ...]:
        found: list[str] = []
        try:
            for shard in sorted(self.root.iterdir()):
                # `tmp` is two characters, which is exactly what a shard
                # looks like: named rather than measured, so a half-written
                # upload is never reported as a stored file.
                if not shard.is_dir() or shard.name == STAGING:
                    continue
                found.extend(
                    path.name for path in sorted(shard.iterdir()) if path.is_file()
                )
        except OSError as error:
            raise AttachmentStoreError(
                f"attachment root {self.root} could not be listed"
            ) from error
        return tuple(found)

    def _path(self, guid: str) -> Path:
        if len(guid) != 36 or "/" in guid or "\\" in guid or guid.startswith("."):
            raise AttachmentStoreError(f"{guid!r} is not an attachment key")
        return self.root / guid[:SHARD_CHARACTERS] / guid


__all__ = ["FilesystemAttachmentBytes"]
