"""Verify S3/MinIO upload, presigning, and download before streaming images."""

from __future__ import annotations

from common.object_storage import ObjectStorageClient, ObjectStorageError
from config.settings import settings


def main() -> None:
    payload = b"disaster-streaming-object-storage-test"
    key = "_system/connection-test.txt"
    print(f"Testing object storage at {settings.object_storage_endpoint_url or 'AWS S3'}...")
    try:
        storage = ObjectStorageClient()
        stored = storage.upload(key, payload, "text/plain")
        downloaded = storage.download(key)
    except ObjectStorageError as exc:
        raise SystemExit(f"FAILED: {exc}") from exc
    if downloaded != payload:
        raise SystemExit("FAILED: downloaded bytes did not match uploaded bytes")
    print(f"SUCCESS: upload and download worked for {stored.uri}")


if __name__ == "__main__":
    main()

