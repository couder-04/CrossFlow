"""CrossFlow: sense the city (CrossSight), then decide who gets green (XtraFlow).

Subpackages
-----------
``crossflow.bridge``    FlowWindow records -> XtraFlow demand calibration inputs
``crossflow.city``      offline city simulation that produces camera flow (no databases)
``crossflow.signals``   read XtraFlow results; expose them to the API and dashboard
``crossflow.pipeline``  one command: city flow -> measured demand -> signal-control study
"""

__version__ = "1.0.0"
