"""S3-compatible storage backend — requires ``pip install boto3``.

Wire it up explicitly::

    from adminfoundry.storage import configure
    from adminfoundry.extensions.storage_s3 import S3Storage

    configure(S3Storage(bucket="my-bucket", region="us-east-1"))
"""
from __future__ import annotations

from typing import BinaryIO


class S3Storage:
    """S3-compatible storage — requires ``pip install boto3``."""

    def __init__(
        self,
        bucket: str,
        *,
        region: str = "us-east-1",
        base_url: str | None = None,
        endpoint_url: str | None = None,
    ) -> None:
        try:
            import boto3
        except ImportError as exc:
            raise RuntimeError(
                "S3 storage requires boto3: pip install boto3"
            ) from exc
        self.bucket = bucket
        self._s3 = boto3.client("s3", region_name=region, endpoint_url=endpoint_url)
        self._base_url = base_url or f"https://{bucket}.s3.{region}.amazonaws.com"

    async def save(self, path: str, file: BinaryIO) -> str:
        self._s3.upload_fileobj(file, self.bucket, path)
        return path

    async def delete(self, path: str) -> None:
        self._s3.delete_object(Bucket=self.bucket, Key=path)

    def url(self, path: str) -> str:
        return f"{self._base_url}/{path}"


__all__ = ["S3Storage"]
