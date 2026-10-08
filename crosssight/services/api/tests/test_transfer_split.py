"""Transfer routes live in four modules with the same URL paths."""

from api.routes.evidence import router as evidence_router
from api.routes.exports import router as exports_router
from api.routes.imports import router as imports_router
from api.routes.uploads import router as uploads_router


def _paths(router) -> set[tuple[str, str]]:
    found = set()
    for route in router.routes:
        for method in route.methods or ():
            if method in {"HEAD", "OPTIONS"}:
                continue
            found.add((method, route.path))
    return found


def test_transfer_paths_are_split_across_modules() -> None:
    assert _paths(imports_router) == {
        ("POST", "/imports/preview"),
        ("POST", "/imports/{upload_id}/confirm"),
        ("GET", "/imports"),
        ("GET", "/imports/{upload_id}"),
    }
    assert _paths(uploads_router) == {
        ("POST", "/uploads/media"),
        ("POST", "/uploads/{upload_id}/retry"),
    }
    assert _paths(exports_router) == {
        ("POST", "/exports"),
        ("POST", "/exports/{export_id}/retry"),
        ("GET", "/exports"),
        ("GET", "/exports/{export_id}"),
        ("GET", "/exports/{export_id}/download"),
    }
    assert _paths(evidence_router) == {
        ("GET", "/alerts/{alert_id}/evidence"),
        ("GET", "/alerts/{alert_id}/evidence/{kind}"),
        ("POST", "/alerts/{alert_id}/evidence/clip"),
    }
