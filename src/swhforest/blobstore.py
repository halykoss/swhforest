"""Blob layer: Software Heritage content, addressed by ``blob_id``.

Software Heritage publishes every content object in the public bucket
``softwareheritage`` under ``content/<sha1_git>``, gzip-compressed.  That is
the same object that ``blob_id`` in stack-edu / The Stack v2 points at, so
SWHForest never depends on a local copy of the corpus: text is fetched on
demand and can be dropped again.

``reopen()`` is the only path to the text.  Queries must not call it.
"""

from __future__ import annotations

import gzip
import os
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

S3_URL = "https://softwareheritage.s3.amazonaws.com/content/{blob_id}"


class BlobStore:
    def __init__(self, cache_dir: str, workers: int = 32) -> None:
        self.cache_dir = cache_dir
        self.workers = workers
        os.makedirs(cache_dir, exist_ok=True)
        self.reopens = 0
        self.reopened_bytes = 0
        self.fetches = 0
        self.misses: list[str] = []
        self._lock = threading.Lock()

    def path(self, blob_id: str) -> str:
        return os.path.join(self.cache_dir, blob_id + ".gz")

    def has(self, blob_id: str) -> bool:
        return os.path.exists(self.path(blob_id))

    def _download(self, blob_id: str) -> bool:
        url = S3_URL.format(blob_id=blob_id)
        for attempt in range(4):
            try:
                with urllib.request.urlopen(url, timeout=60) as r:
                    data = r.read()
                with open(self.path(blob_id), "wb") as f:
                    f.write(data)
                with self._lock:
                    self.fetches += 1
                return True
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    with self._lock:
                        self.misses.append(blob_id)
                    return False
                if e.code < 500:
                    raise
                time.sleep(1.5 ** attempt)
            except Exception:
                time.sleep(1.5 ** attempt)
        with self._lock:
            self.misses.append(blob_id)
        return False

    def ensure_local(self, blob_ids: list[str]) -> list[str]:
        missing = [b for b in blob_ids if not self.has(b)]
        if missing:
            with ThreadPoolExecutor(max_workers=self.workers) as ex:
                list(ex.map(self._download, missing))
        return [b for b in blob_ids if self.has(b)]

    def reopen(self, blob_id: str) -> bytes:
        """Read one blob's bytes.  This is the costly operation being counted."""
        with open(self.path(blob_id), "rb") as f:
            raw = f.read()
        data = gzip.decompress(raw)
        with self._lock:
            self.reopens += 1
            self.reopened_bytes += len(data)
        return data

    def reset_counters(self) -> None:
        self.reopens = 0
        self.reopened_bytes = 0
