"""Size bucket pattern."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Literal

import numpy as np

from metricstudio.patterns.base import BasePattern


class SizeBucket(BasePattern):
    """
    시가총액 bucket mask를 그대로 패턴으로 노출한다.
    """

    def on(
        self,
        bucket: Literal["large", "mid", "small"],
    ):
        """
        large/mid/small bucket 중 하나를 선택한다.
        """

        key = str(bucket or "").strip().lower()
        if key not in {"large", "mid", "small"}:
            raise ValueError("bucket은 'large', 'mid', 'small' 중 하나여야 합니다.")
        self.params = SimpleNamespace(bucket=key)
        return self

    def _required_stock_fields(self) -> tuple[str, ...]:
        if self.params is None:
            return ()
        return (f"size_bucket_{self.params.bucket}",)

    def _base_mask(self, values: np.ndarray) -> np.ndarray:
        if self.params is None:
            raise ValueError("SizeBucket은 사용 전에 on(...)으로 설정해야 합니다.")
        mask_values = self._get_stock_values(f"size_bucket_{self.params.bucket}")
        if mask_values.shape != np.asarray(values).shape:
            raise ValueError("SizeBucket mask shape이 가격 시계열과 일치하지 않습니다.")
        return np.asarray(mask_values, dtype=np.bool_)


__all__ = ["SizeBucket"]
