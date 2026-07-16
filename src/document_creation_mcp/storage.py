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
    # Access/secret keys are optional: when unset we connect anonymously
    # (e.g. an open instance or one fronted by an authenticating proxy).
    # When set they are passed through as the S3 access key id / secret
    # access key (the same credentials n8n's S3 node uses).
    kwargs = {
        "secure": settings.minio_use_https,
        "region": settings.minio_region or "us-east-1",
    }
    if settings.minio_access_key:
        kwargs["access_key"] = settings.minio_access_key
    if settings.minio_secret_key:
        kwargs["secret_key"] = settings.minio_secret_key
    return Minio(settings.minio_endpoint, **kwargs)


def upload_file(local_path: Path, bucket_override: str | None = None) -> str:
    """Upload a file to MinIO and return a retrievable URL.

    Uses the bucket from env (``MINIO_BUCKET``) or *bucket_override* when given.
    Returns a public URL when ``MINIO_PUBLIC_URL`` is set, otherwise a presigned
    GET URL valid for ``MINIO_PRESIGNED_EXPIRY_HOURS``.

    When a reverse proxy fronts MinIO (the usual Docker setup), the bucket is
    addressed as a path segment, so the public URL is built as
    ``{MINIO_PUBLIC_URL}/{bucket}/{object_name}``. Set ``MINIO_PUBLIC_INCLUDES_BUCKET=true``
    only if your public URL already embeds the bucket.

    Objects are uploaded with a public-read grant (``MINIO_PUBLIC_READ=true``,
    the default) so a browser / Open WebUI can fetch the deck directly — the
    same behaviour as n8n's S3 node ``grantRead: true``.
    """
    from datetime import timedelta

    settings = get_settings()
    client = _client()
    bucket = bucket_override or settings.minio_bucket
    object_name = f"{settings.minio_prefix}{Path(local_path).name}".replace("//", "/").strip("/")

    try:
        if not client.bucket_exists(bucket):
            client.make_bucket(bucket)
    except Exception:
        # BucketAlreadyOwnedByYou / BucketAlreadyExists / anonymous access
        # restrictions are all non-fatal here — fput_object will surface a
        # real error if the bucket truly cannot be used.
        pass

    # fput_object raises on any real failure (auth, missing bucket, network);
    # let it propagate so callers can report a clear minio_error instead of
    # silently falling back to an unreachable local path.
    extra_args = {"GrantRead": "id=*"} if settings.minio_public_read else None
    client.fput_object(bucket, object_name, str(local_path), extra_args=extra_args)

    if settings.minio_public_url:
        public = settings.minio_public_url.rstrip("/")
        if settings.minio_public_includes_bucket:
            return f"{public}/{object_name}"
        return f"{public}/{bucket}/{object_name}"
    return client.presigned_get_object(
        bucket,
        object_name,
        expires=timedelta(hours=settings.minio_presigned_expiry_hours),
    )
