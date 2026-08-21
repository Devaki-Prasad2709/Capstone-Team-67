"""S3-compatible image upload/download used by efficient transfer mode."""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO

import boto3
from botocore.client import Config
from botocore.exceptions import BotoCoreError, ClientError

from config.settings import settings


@dataclass(frozen=True)
class StoredObject:
    bucket: str
    key: str
    uri: str
    download_url: str


class ObjectStorageError(RuntimeError):
    pass


class ObjectStorageClient:
    """Minimal S3 API wrapper compatible with AWS S3 and local MinIO."""

    def __init__(self) -> None:
        if not settings.object_storage_access_key or not settings.object_storage_secret_key:
            raise ObjectStorageError(
                "Object storage credentials are missing. Set OBJECT_STORAGE_ACCESS_KEY "
                "and OBJECT_STORAGE_SECRET_KEY in .env."
            )
        self.bucket = settings.object_storage_bucket
        self.client = boto3.client(
            "s3",
            endpoint_url=settings.object_storage_endpoint_url or None,
            region_name=settings.object_storage_region,
            aws_access_key_id=settings.object_storage_access_key,
            aws_secret_access_key=settings.object_storage_secret_key,
            config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
        )
        self._ensure_bucket()

    def _ensure_bucket(self) -> None:
        try:
            self.client.head_bucket(Bucket=self.bucket)
        except ClientError as exc:
            status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
            if status not in {400, 403, 404}:
                raise ObjectStorageError(f"Cannot access bucket {self.bucket}: {exc}") from exc
            try:
                if settings.object_storage_region == "us-east-1":
                    self.client.create_bucket(Bucket=self.bucket)
                else:
                    self.client.create_bucket(
                        Bucket=self.bucket,
                        CreateBucketConfiguration={
                            "LocationConstraint": settings.object_storage_region
                        },
                    )
            except (BotoCoreError, ClientError) as create_exc:
                raise ObjectStorageError(
                    f"Cannot create/access object-storage bucket {self.bucket}: {create_exc}"
                ) from create_exc

    def upload(self, key: str, data: bytes, content_type: str) -> StoredObject:
        try:
            self.client.put_object(
                Bucket=self.bucket,
                Key=key,
                Body=data,
                ContentType=content_type,
            )
            url = self.client.generate_presigned_url(
                "get_object",
                Params={"Bucket": self.bucket, "Key": key},
                ExpiresIn=max(60, min(settings.object_storage_presigned_expiry, 604800)),
            )
        except (BotoCoreError, ClientError) as exc:
            raise ObjectStorageError(f"Object upload failed for {key}: {exc}") from exc
        return StoredObject(self.bucket, key, f"s3://{self.bucket}/{key}", url)

    def download(self, key: str) -> bytes:
        try:
            buffer = BytesIO()
            self.client.download_fileobj(self.bucket, key, buffer)
            return buffer.getvalue()
        except (BotoCoreError, ClientError) as exc:
            raise ObjectStorageError(f"Object download failed for {key}: {exc}") from exc

