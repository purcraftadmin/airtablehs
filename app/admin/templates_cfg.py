"""
Shared Jinja2Templates instance for admin UI.
Defined here to avoid circular imports.
"""
from __future__ import annotations

import pathlib

from fastapi.templating import Jinja2Templates

templates = Jinja2Templates(
    directory=str(pathlib.Path(__file__).parent.parent / "templates")
)
