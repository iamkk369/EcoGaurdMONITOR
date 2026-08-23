"""EcoGuardAI agents package."""
# Agents are imported on demand.  Eager imports here would pull in
# scikit-learn, loguru, and other heavy deps at package-import time,
# breaking scripts that only need a subset of the project.
