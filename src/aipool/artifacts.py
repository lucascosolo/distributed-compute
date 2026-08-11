"""Content-addressed storage for task inputs and outputs."""

from __future__ import annotations

import hashlib
from pathlib import Path


class ArtifactStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def put(self, data: bytes) -> str:
        digest = hashlib.sha256(data).hexdigest()
        path = self.root / digest
        if not path.exists():
            path.write_bytes(data)
        return f"artifact:sha256:{digest}"

    def get(self, reference: str) -> bytes:
        prefix, separator, digest = reference.partition("artifact:sha256:")
        if prefix or not separator or len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            raise ValueError("invalid artifact reference")
        data = (self.root / digest).read_bytes()
        if hashlib.sha256(data).hexdigest() != digest:
            raise ValueError("artifact integrity check failed")
        return data
