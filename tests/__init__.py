"""
테스트 실행 시 matplotlib/fontconfig 캐시 경로를 writable 디렉터리로 고정한다.
"""

from __future__ import annotations

import os
from pathlib import Path
import tempfile


def _configure_test_cache_dirs() -> None:
    cache_root = Path(tempfile.gettempdir()) / "metricstudio-test-cache"
    mpl_cache_dir = cache_root / "matplotlib"
    xdg_cache_dir = cache_root / "xdg-cache"

    mpl_cache_dir.mkdir(parents=True, exist_ok=True)
    (xdg_cache_dir / "fontconfig").mkdir(parents=True, exist_ok=True)

    os.environ.setdefault("MPLCONFIGDIR", str(mpl_cache_dir))
    os.environ.setdefault("XDG_CACHE_HOME", str(xdg_cache_dir))


_configure_test_cache_dirs()
