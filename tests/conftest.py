"""No test may ever start a real FLUX run: it needs ~17 GB of GPU memory and took the machine down once."""
import pytest


@pytest.fixture(autouse=True)
def _never_run_real_flux(monkeypatch):
    try:
        import imagegen
    except Exception:
        return
    monkeypatch.setattr(imagegen, "MFLUX_BIN", "/nonexistent/mflux-generate-blocked-in-tests")
