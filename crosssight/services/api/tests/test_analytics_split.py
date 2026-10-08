"""Analytics routes keep their URLs after the module split."""

from api.routes.analytics_flow import router as flow_router
from api.routes.analytics_heatmap import router as heatmap_router
from api.routes.analytics_od import router as od_router


def _paths(router) -> set[tuple[str, str]]:
    found = set()
    for route in router.routes:
        for method in route.methods or ():
            if method in {"HEAD", "OPTIONS"}:
                continue
            found.add((method, route.path))
    return found


def test_analytics_paths_are_split_across_modules() -> None:
    assert _paths(heatmap_router) == {("GET", "/analytics/heatmap")}
    assert _paths(flow_router) == {
        ("GET", "/analytics/flow"),
        ("GET", "/analytics/segments"),
        ("GET", "/analytics/bottlenecks"),
        ("GET", "/analytics/anomalies"),
        ("GET", "/analytics/route-density"),
    }
    assert _paths(od_router) == {("GET", "/analytics/od")}
