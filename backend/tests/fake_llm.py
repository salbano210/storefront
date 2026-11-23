"""The app's simulated model, under the name the tests have always imported it by.

It now lives in ``app/services/simulated_model.py`` because the sample-history
seeder drives the real orchestrator with it; a second implementation would drift
from the one the tests pin.
"""

from app.services.simulated_model import SimulatedModel as FakeLLM  # noqa: F401
