"""Universe configuration objects for backtests."""

from __future__ import annotations

from dataclasses import dataclass

from metricstudio.dataload import DEFAULT_DEPT_EXCLUDES, DEFAULT_MARKETS


def _normalize_univ_markets(values) -> tuple[str, ...] | None:
    """
    Univ.market 입력을 중복 없는 대문자 튜플로 정규화한다.
    """

    if values is None:
        return None
    if isinstance(values, str):
        items = [values]
    else:
        items = list(values)
    out: list[str] = []
    for item in items:
        text = str(item).strip().upper()
        if text and text not in out:
            out.append(text)
    if not out:
        raise ValueError("market은 비어 있을 수 없습니다.")
    return tuple(out)


def _normalize_univ_depts(values) -> tuple[str, ...]:
    """
    Univ.dept_excludes 입력을 순서 보존 튜플로 정규화한다.
    """

    if values is None:
        return ()
    if isinstance(values, str):
        items = [values]
    else:
        items = list(values)
    out: list[str] = []
    for item in items:
        text = str(item).strip()
        if text and text not in out:
            out.append(text)
    return tuple(out)


@dataclass(frozen=True)
class Univ:
    """
    백테스트 시 유니버스 필터 조건을 담는 불변 설정 객체.
    """

    market: tuple[str, ...] | None = DEFAULT_MARKETS
    is_tradable: bool | None = True
    dept_excludes: tuple[str, ...] = DEFAULT_DEPT_EXCLUDES
    exclude_reits: bool = True

    def __init__(
        self,
        market=DEFAULT_MARKETS,
        is_tradable: bool | None = True,
        dept_excludes=DEFAULT_DEPT_EXCLUDES,
        exclude_reits: bool = True,
    ):
        object.__setattr__(self, "market", _normalize_univ_markets(market))
        object.__setattr__(self, "is_tradable", None if is_tradable is None else bool(is_tradable))
        object.__setattr__(self, "dept_excludes", _normalize_univ_depts(dept_excludes))
        object.__setattr__(self, "exclude_reits", bool(exclude_reits))

    def cache_key(self) -> tuple[tuple[str, ...] | None, bool | None, tuple[str, ...], bool]:
        """
        유니버스별 캐시 키를 만든다.
        """

        return self.market, self.is_tradable, self.dept_excludes, self.exclude_reits


__all__ = ["Univ"]
