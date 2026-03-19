"""All-stock benchmark pattern."""

from __future__ import annotations

from metricstudio.patterns.base import BasePattern


class AllStockPattern(BasePattern):
    """
    모든 유효 가격 구간을 benchmark로 선택하는 기본 패턴.
    """


__all__ = ["AllStockPattern"]
