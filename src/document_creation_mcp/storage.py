from __future__ import annotations

from pathlib import Path

from .config import get_settings


def _client():
    try:
        from minio import Minio
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "The 'minio' package is required for MinIO upload. "
            "Install with: pip install minio"
        ) from exc

    settings = get_settings()
    return Minio(
        settings.minio_endpoint,
        access_key=settings.minio_access_key,
        secret_key=settings.minio_secret_key,
        secure=settings.minio_use_https,
        region=settings.minio_region,
    )


def upload_file(local_path: Path, bucket_override: str | None = None) -> str:
    """Upload a file to MinIO and return a retrievable URL.

    Uses the bucket from env (``MINIO_BUCKET``) or *bucket_override* when given.
    Returns a public URL when ``MINIO_PUBLIC_URL`` is set, otherwise a presigned
    GET URL valid for ``MINIO_PRESIGNED_EXPIRY_HOURS``.
    """
    from datetime import timedelta

    settings = get_settings()
    client = _client()
    bucket = bucket_override or settings.minio_bucket
    object_name = f"{settings.minio_prefix}{Path(local_path).name}".replace("//", "/")

    try:
        client.make_bucket(bucket)
    except Exception:
        # BucketAlreadyOwnedByYou / BucketAlreadyExists are expected.
        pass

    client.fput_object(bucket, object_name, str(local_path))

    if settings.minio_public_url:
        return f"{settings.minio_public_url.rstrip('/')}/{object_name}"
    return client.presigned_get_object(
        bucket,
        object_name,
        expires=timedelta(hours=settings.minio_presigned_expiry_hours),
    )
