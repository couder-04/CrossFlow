"""Presigned MinIO URLs must be signed for the host the browser can reach."""

from anpr_common.config import Settings
from api import deps


def _presigned_url(**env: str) -> str:
    deps._minio_presign_client = None  # the client is cached per process
    # pydantic-settings coerces/validates the env-style kwargs at runtime
    settings = Settings(MINIO_ACCESS_KEY="k", MINIO_SECRET_KEY="s", **env)  # type: ignore[arg-type]
    client = deps.get_minio_presign(settings)
    return client.presigned_get_object("anpr-crops", "frames/latest/cam-1.jpg")


def test_signed_for_public_endpoint_not_docker_hostname():
    # Region is fixed, so this must not try to reach either host.
    url = _presigned_url(MINIO_ENDPOINT="minio:9000", MINIO_PUBLIC_ENDPOINT="localhost:9000")
    assert url.startswith("http://localhost:9000/anpr-crops/frames/latest/cam-1.jpg?")
    assert "minio:9000" not in url


def test_falls_back_to_minio_endpoint():
    url = _presigned_url(MINIO_ENDPOINT="storage.internal:9000", MINIO_PUBLIC_ENDPOINT="")
    assert url.startswith("http://storage.internal:9000/")
