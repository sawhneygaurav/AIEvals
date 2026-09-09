"""Private, local RAG index for *Peaceful Investing*.

RAG means "retrieve, then use": instead of placing a 271-page book in every
prompt, we split it into small page-aware chunks and retrieve only the relevant
ones.  Chroma stores the index on this computer.  The PDF and extracted text are
never copied into Git.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path

from pypdf import PdfReader

from .tracing import event, traced


@dataclass(frozen=True, slots=True)
class Chapter:
    title: str
    printed_start: int
    printed_end: int


# The ranges come from the book's own table of contents.  PDF page numbers are
# one greater than printed page numbers for this particular edition.
CHAPTERS: tuple[Chapter, ...] = (
    Chapter("Getting the Right Perspective towards Investing", 4, 6),
    Chapter("Choosing the Stock Picking Approach suitable to you", 7, 12),
    Chapter("Why I Left Technical Analysis And Never Returned To It", 13, 17),
    Chapter("Shortlisting Companies for Detailed Analysis", 18, 24),
    Chapter("How to conduct Detailed Analysis of a Company", 25, 30),
    Chapter("Understanding the Annual Report of a Company", 31, 42),
    Chapter("How to do Financial Analysis of a Company", 43, 51),
    Chapter("Financial Shenanigans", 52, 59),
    Chapter("Self-Sustainable Growth Rate", 60, 67),
    Chapter("How to do Valuation Analysis of a Company", 68, 72),
    Chapter("Hidden Risk of Investing in High P/E Stocks", 73, 80),
    Chapter("High Returns at Low Risk - Low P/E Stocks", 81, 87),
    Chapter("Principles to Decide the Investable P/E Ratio", 88, 103),
    Chapter("Business & Industry Analysis", 104, 112),
    Chapter("Is Industry P/E Ratio Relevant?", 113, 121),
    Chapter("Why Management Assessment is Most Critical", 122, 133),
    Chapter("Assess Management Quality - Part 1", 134, 144),
    Chapter("Assess Management Quality - Part 2", 145, 156),
    Chapter("Assess Management Quality - Part 3", 157, 161),
    Chapter("Margin of Safety", 162, 171),
    Chapter("Why Investors Should Read Credit Rating Reports", 172, 181),
    Chapter("Final Checklist for Buying Stocks", 182, 186),
    Chapter("Analyse Operating Performance", 187, 193),
    Chapter("How to Monitor Stocks in Your Portfolio", 194, 198),
    Chapter("Understanding Quarterly Results", 199, 211),
    Chapter("How Many Stocks Should You Own", 212, 216),
    Chapter("Trading Diary of a Value Investor", 217, 223),
    Chapter("When to Sell a Stock", 224, 228),
    Chapter("Stocks Ideal for Retail Equity Investors", 229, 236),
    Chapter("Using Screener.in Export to Excel", 237, 258),
    Chapter("Premium Services", 259, 270),
)


def chapter_for_page(printed_page: int) -> str:
    """Resolve a printed page number to a human-readable chapter."""

    for chapter in CHAPTERS:
        if chapter.printed_start <= printed_page <= chapter.printed_end:
            return chapter.title
    return "Front matter"


class HashingEmbedder:
    """Tiny, deterministic local embeddings with no model download.

    This is feature hashing: tokens that occur in similar passages produce
    similar vectors.  It is intentionally modest but is a good privacy-first
    baseline for one structured book.  A production team can later swap in a
    stronger local embedding model without changing the RAG interface.
    """

    TOKEN_PATTERN = re.compile(r"[a-z][a-z0-9'-]{1,}")

    def __init__(self, dimensions: int = 512) -> None:
        self.dimensions = dimensions

    def embed(self, text: str) -> list[float]:
        tokens = self.TOKEN_PATTERN.findall(text.lower())
        # Bigrams preserve a little phrase meaning (for example "cash flow").
        features = tokens + [f"{a}_{b}" for a, b in pairwise(tokens)]
        vector = [0.0] * self.dimensions
        for feature in features:
            digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
            bucket = int.from_bytes(digest[:4], "big") % self.dimensions
            sign = 1.0 if digest[4] & 1 else -1.0
            vector[bucket] += sign

        norm = math.sqrt(sum(value * value for value in vector))
        if norm:
            vector = [value / norm for value in vector]
        return vector

    def embed_many(self, texts: Iterable[str]) -> list[list[float]]:
        return [self.embed(text) for text in texts]


@dataclass(frozen=True, slots=True)
class RetrievedChunk:
    chunk_id: str
    document: str
    pdf_page: int
    printed_page: int
    chapter: str
    distance: float


def _clean_page_text(text: str) -> str:
    """Remove repeated headers/footers that otherwise dominate retrieval."""

    kept: list[str] = []
    for line in text.replace("\x00", " ").splitlines():
        stripped = " ".join(line.split())
        lowered = stripped.lower()
        if not stripped:
            continue
        if lowered == "www.drvijaymalik.com":
            continue
        if lowered.startswith("copyright ©"):
            continue
        if re.fullmatch(r"\d+\s*\|\s*p\s*a\s*g\s*e", lowered):
            continue
        kept.append(stripped)
    return "\n".join(kept)


def _split_text(text: str, *, size: int = 1400, overlap: int = 180) -> list[str]:
    """Create overlapping chunks while avoiding cuts in the middle of words."""

    if len(text) <= size:
        return [text] if text.strip() else []

    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text):
            # Prefer the last whitespace near the boundary.
            boundary = text.rfind(" ", start + size // 2, end)
            if boundary > start:
                end = boundary
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(text):
            break
        start = max(start + 1, end - overlap)
    return chunks


class BookKnowledgeBase:
    """Build and query the local Chroma collection."""

    collection_name = "peaceful_investing_v1"

    def __init__(self, *, pdf_path: Path, index_dir: Path) -> None:
        self.pdf_path = Path(pdf_path).expanduser().resolve()
        self.index_dir = Path(index_dir).expanduser().resolve()
        self.embedder = HashingEmbedder()

    @property
    def manifest_path(self) -> Path:
        return self.index_dir / "manifest.json"

    @traced("book.hash")
    def document_hash(self) -> str:
        """Hash the bytes so a changed edition automatically rebuilds its index."""

        digest = hashlib.sha256()
        with self.pdf_path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()

    def _client(self):
        # The import is local so simple scoring/unit tests can run without loading
        # Chroma's heavier dependencies.
        import chromadb
        from chromadb.config import Settings as ChromaSettings

        self.index_dir.mkdir(parents=True, exist_ok=True)
        return chromadb.PersistentClient(
            path=str(self.index_dir),
            # The book is private. Disable Chroma's optional anonymous telemetry
            # in addition to keeping all documents and embeddings on disk locally.
            settings=ChromaSettings(anonymized_telemetry=False),
        )

    def _collection(self):
        return self._client().get_or_create_collection(
            self.collection_name,
            metadata={"hnsw:space": "cosine", "source": "private-local-pdf"},
        )

    @traced("book.ensure_index")
    def ensure_index(self, *, force: bool = False) -> dict[str, int | str]:
        """Create the local index once, then reuse it on future app runs."""

        if not self.pdf_path.exists():
            raise FileNotFoundError(f"Book not found: {self.pdf_path}")

        current_hash = self.document_hash()
        collection = self._collection()
        if not force and self.manifest_path.exists():
            manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
            if manifest.get("document_hash") == current_hash and collection.count() > 0:
                event("book.index", outcome="reused", pages=int(manifest["pages"]),
                      chunks=collection.count())
                return {
                    "status": "ready",
                    "pages": int(manifest["pages"]),
                    "chunks": collection.count(),
                    "document_hash": current_hash,
                }

        # A force rebuild deletes only this named collection inside the private
        # index directory; it never deletes the PDF itself.
        client = self._client()
        existing_names = {item.name for item in client.list_collections()}
        if self.collection_name in existing_names:
            client.delete_collection(self.collection_name)
        collection = client.create_collection(
            self.collection_name,
            metadata={"hnsw:space": "cosine", "source": "private-local-pdf"},
        )

        event("book.index", outcome="rebuilding")
        reader = PdfReader(str(self.pdf_path))
        ids: list[str] = []
        documents: list[str] = []
        metadatas: list[dict[str, int | str]] = []
        for page_index, page in enumerate(reader.pages):
            pdf_page = page_index + 1
            printed_page = max(0, pdf_page - 1)
            text = _clean_page_text(page.extract_text() or "")
            for chunk_number, chunk in enumerate(_split_text(text)):
                ids.append(f"p{pdf_page:03d}-c{chunk_number:02d}")
                documents.append(chunk)
                metadatas.append(
                    {
                        "pdf_page": pdf_page,
                        "printed_page": printed_page,
                        "chapter": chapter_for_page(printed_page),
                        "document_hash": current_hash,
                    }
                )

        # Batching keeps memory use small and also stays below Chroma limits.
        for start in range(0, len(ids), 100):
            end = start + 100
            collection.add(
                ids=ids[start:end],
                documents=documents[start:end],
                metadatas=metadatas[start:end],
                embeddings=self.embedder.embed_many(documents[start:end]),
            )

        self.index_dir.mkdir(parents=True, exist_ok=True)
        self.manifest_path.write_text(
            json.dumps(
                {
                    "document_hash": current_hash,
                    "pages": len(reader.pages),
                    "chunks": len(ids),
                    "embedding": "local-feature-hashing-v1",
                    "page_convention": "pdf_page = printed_page + 1",
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        return {
            "status": "built",
            "pages": len(reader.pages),
            "chunks": len(ids),
            "document_hash": current_hash,
        }

    @traced("book.retrieve")
    def retrieve(
        self,
        query: str,
        *,
        printed_ranges: Sequence[tuple[int, int]],
        per_range: int = 2,
    ) -> list[RetrievedChunk]:
        """Retrieve relevant chunks, constrained to known methodology chapters."""

        self.ensure_index()
        collection = self._collection()
        candidates: list[RetrievedChunk] = []
        query_embedding = [self.embedder.embed(query)]

        for start, end in printed_ranges:
            result = collection.query(
                query_embeddings=query_embedding,
                n_results=per_range,
                where={
                    "$and": [
                        {"printed_page": {"$gte": start}},
                        {"printed_page": {"$lte": end}},
                    ]
                },
                include=["documents", "metadatas", "distances"],
            )
            result_ids = result.get("ids", [[]])[0]
            result_docs = result.get("documents", [[]])[0]
            result_meta = result.get("metadatas", [[]])[0]
            result_distances = result.get("distances", [[]])[0]
            for item_id, document, metadata, distance in zip(
                result_ids, result_docs, result_meta, result_distances
            ):
                candidates.append(
                    RetrievedChunk(
                        chunk_id=item_id,
                        document=document or "",
                        pdf_page=int(metadata["pdf_page"]),
                        printed_page=int(metadata["printed_page"]),
                        chapter=str(metadata["chapter"]),
                        distance=float(distance),
                    )
                )

        # The same chunk can match two overlapping ranges.  Keep only its best hit.
        unique: dict[str, RetrievedChunk] = {}
        for item in sorted(candidates, key=lambda candidate: candidate.distance):
            # `setdefault` preserves the lower-distance (better) duplicate.
            unique.setdefault(item.chunk_id, item)
        return list(unique.values())
