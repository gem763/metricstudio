from __future__ import annotations

from tqdm import tqdm


def progress(*args, **kwargs):
    """
    노트북 저장 시 widget 출력이 남지 않도록 일반 tqdm 기본값을 적용한다.
    """

    kwargs.setdefault("leave", True)
    kwargs.setdefault("dynamic_ncols", True)
    return tqdm(*args, **kwargs)
