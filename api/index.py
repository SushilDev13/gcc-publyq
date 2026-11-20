"""Vercel entry point: the FastAPI app as one Python function.
vercel.json rewrites /api/*, /v1/* and /chat/* here with the path preserved."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.app.main import app  # noqa: E402,F401
