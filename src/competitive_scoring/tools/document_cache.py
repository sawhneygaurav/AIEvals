"""Best-effort local cache for public filings, never a source of freshness.

The collector must revalidate a document with its origin before using its bytes.
Content-addressed blobs and atomic manifests keep concurrent runs consistent.
Extracted excerpts are also keyed by parser version, extraction policy and limits.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

from pypdf import __version__ as PYPDF_VERSION

from ..tracing import event

# Bump whenever page selection, table formatting, safety limits or excerpts change.
EXTRACTION_VERSION = "selected-layout-v1"
_DIGEST = re.compile(r"[0-9a-f]{64}")


def digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


@dataclass(frozen=True)
class CachedDocument:
    content: bytes
    final_url: str
    content_type: str
    etag: str = ""
    last_modified: str = ""

    @property
    def validators(self) -> dict[str, str]:
        return {
            header: value
            for header, value in (
                ("If-None-Match", self.etag),
                ("If-Modified-Since", self.last_modified),
            )
            if value
        }


class DocumentCache:
    def __init__(self, directory: Path) -> None:
        self.directory = directory

    @classmethod
    def from_env(cls) -> DocumentCache | None:
        configured = os.getenv("COMPETITIVE_SCORING_DOCUMENT_CACHE_DIR", "").strip()
        if configured.lower() == "off":
            return None
        project_root = Path(__file__).resolve().parents[3]
        directory = Path(configured).expanduser() if configured else Path("outputs/document_cache")
        if not directory.is_absolute():
            directory = project_root / directory
        return cls(directory)

    def _manifest(self, url: str) -> Path:
        return self.directory / "documents" / f"{digest(url.encode())}.json"

    def load_document(self, url: str, *, max_bytes: int) -> CachedDocument | None:
        try:
            record = self._read_json(self._manifest(url), limit=16_384)
            content_hash = record["content_hash"]
            if (
                record["version"] != 1 or record["origin_url"] != url
                or not _DIGEST.fullmatch(content_hash)
            ):
                raise ValueError("invalid cache manifest")
            path = self.directory / "blobs" / f"{content_hash}.pdf"
            with path.open("rb") as handle:
                content = handle.read(max_bytes + 1)
            if len(content) > max_bytes or digest(content) != content_hash:
                raise ValueError("invalid cached content")
            fields = {key: record[key] for key in (
                "final_url", "content_type", "etag", "last_modified"
            )}
            if any(not isinstance(value, str) for value in fields.values()):
                raise ValueError("invalid cache metadata")
            # Persist only single-line, bounded HTTP validators.
            for key in ("etag", "last_modified"):
                if not _valid_validator(fields[key]):
                    raise ValueError("invalid cache validator")
            return CachedDocument(content=content, **fields)
        except FileNotFoundError:
            return None
        except (OSError, ValueError, KeyError, TypeError):
            event("diagnostic", code="DOCUMENT_CACHE_READ_FAILED")
            return None

    def save_document(self, url: str, document: CachedDocument) -> None:
        content_hash = digest(document.content)
        record = {
            "version": 1,
            "origin_url": url,
            "content_hash": content_hash,
            "final_url": document.final_url,
            "content_type": document.content_type,
            "etag": document.etag if _valid_validator(document.etag) else "",
            "last_modified": (
                document.last_modified if _valid_validator(document.last_modified) else ""
            ),
        }
        try:
            self._atomic_write(self.directory / "blobs" / f"{content_hash}.pdf", document.content)
            self._atomic_write(self._manifest(url), json.dumps(record).encode())
        except OSError:
            event("diagnostic", code="DOCUMENT_CACHE_WRITE_FAILED")

    def invalidate_document(self, url: str) -> None:
        try:
            self._manifest(url).unlink(missing_ok=True)
        except OSError:
            event("diagnostic", code="DOCUMENT_CACHE_WRITE_FAILED")

    def _excerpt_path(self, content_hash: str, max_pages: int, max_chars: int) -> Path:
        identity = json.dumps([content_hash, EXTRACTION_VERSION, PYPDF_VERSION, max_pages, max_chars])
        return self.directory / "excerpts" / f"{digest(identity.encode())}.json"

    def load_excerpt(self, content_hash: str, *, max_pages: int, max_chars: int) -> str | None:
        try:
            path = self._excerpt_path(content_hash, max_pages, max_chars)
            record = self._read_json(
                path, limit=max_chars * 6 + 1024
            )
            text = record["text"]
            if (
                not isinstance(text, str) or not text or len(text) > max_chars
                or digest(text.encode()) != record["text_hash"]
                or record["cache_key"] != path.stem
            ):
                raise ValueError("invalid cached excerpt")
            return text
        except FileNotFoundError:
            return None
        except (OSError, ValueError, KeyError, TypeError):
            event("diagnostic", code="PDF_CACHE_READ_FAILED")
            return None

    def save_excerpt(self, content_hash: str, text: str, *, max_pages: int, max_chars: int) -> None:
        path = self._excerpt_path(content_hash, max_pages, max_chars)
        record = {"text": text, "text_hash": digest(text.encode()), "cache_key": path.stem}
        try:
            self._atomic_write(path, json.dumps(record).encode())
        except OSError:
            event("diagnostic", code="PDF_CACHE_WRITE_FAILED")

    @staticmethod
    def _read_json(path: Path, *, limit: int) -> dict:
        with path.open("rb") as handle:
            content = handle.read(limit + 1)
        if len(content) > limit:
            raise ValueError("cache entry too large")
        record = json.loads(content)
        if not isinstance(record, dict):
            raise TypeError("invalid cache entry")
        return record

    @staticmethod
    def _atomic_write(path: Path, content: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".tmp-", delete=False) as handle:
                temporary = Path(handle.name)
                handle.write(content)
            os.replace(temporary, path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)


def _valid_validator(value: str) -> bool:
    return len(value) <= 1024 and all(32 <= ord(char) <= 126 for char in value)
