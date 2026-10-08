"""Bridge: CrossSight flow analytics -> XtraFlow calibration inputs.

CrossSight measures what the city's cameras see. XtraFlow decides who gets green. This
package carries measured traffic from the first into the second:

* ``FlowWindow`` events (``analytics.flow.v1``)  ->  ``observed_counts.csv``
* ``counts_by_class``                            ->  XtraFlow vehicle-mix estimate

Stdlib only.
"""

from .flows import (
    APPROACHES,
    CLASS_MAP,
    FlowError,
    approach_rates,
    load_camera_map,
    load_flow_windows,
    vehicle_mix,
    write_observed_counts,
)

__all__ = [
    "APPROACHES",
    "CLASS_MAP",
    "FlowError",
    "approach_rates",
    "load_camera_map",
    "load_flow_windows",
    "vehicle_mix",
    "write_observed_counts",
]
