"""Switchable Base64 or object-reference transport for image events."""

from __future__ import annotations

import base64
from pathlib import Path

from common.object_storage import ObjectStorageClient
from config.settings import settings


class ImageTransfer:
    def __init__(self) -> None:
        if settings.image_transfer_mode not in {"base64", "object_storage"}:
            raise ValueError("IMAGE_TRANSFER_MODE must be base64 or object_storage")
        self.mode = settings.image_transfer_mode
        self.storage = ObjectStorageClient() if self.mode == "object_storage" else None

    def attach(
        self,
        event: dict[str, object],
        image_bytes: bytes,
        source: str,
        filename: str,
        fingerprint: str,
        content_type: str = "image/jpeg",
    ) -> dict[str, object]:
        event.update(
            {
                "transfer_mode": self.mode,
                "size_bytes": len(image_bytes),
                "content_hash": fingerprint,
                "is_duplicate": False,
            }
        )
        if self.mode == "base64":
            event["image_data"] = base64.b64encode(image_bytes).decode("ascii")
            return event

        safe_name = Path(filename).name
        object_key = f"{source}/{fingerprint[:2]}/{fingerprint}_{safe_name}"
        assert self.storage is not None
        stored = self.storage.upload(object_key, image_bytes, content_type)
        event.update(
            {
                "object_key": stored.key,
                "image_uri": stored.uri,
                "download_url": stored.download_url,
            }
        )
        return event


def duplicate_event_fields(
    canonical_id: str,
    fingerprint: str,
    canonical_fingerprint: str,
    method: str | None,
    distance: int | None,
) -> dict[str, object]:
    return {
        "transfer_mode": "none",
        "is_duplicate": True,
        "canonical_image_id": canonical_id,
        "content_hash": fingerprint,
        "canonical_content_hash": canonical_fingerprint,
        "duplicate_method": method,
        "perceptual_distance": distance,
        "processing_status": "duplicate_skipped",
    }
