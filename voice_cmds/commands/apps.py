"""Open-app dispatcher — platform facade (per-platform launchers behind it)."""
from __future__ import annotations

import logging
import sys

if sys.platform == "darwin":
    from . import _apps_mac as _impl
else:
    from . import _apps_win as _impl


def open_app(entry: dict, logger: logging.Logger) -> None:
    _impl.open_app(entry, logger)
