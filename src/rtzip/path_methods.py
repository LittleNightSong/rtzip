def _remove_consecutive_duplicates(data: bytes, byte: int = b'/'[0]) -> bytes:
    """双指针法，最快实现"""
    if not data:
        return data

    result = bytearray()
    prev = None
    for b in data:
        if b == byte and prev == byte:
            continue  # 跳过重复的
        result.append(b)
        prev = b
    return bytes(result)


def _ensure_end_with_sep(path: bytes) -> bytes:
    return path if path[-1] == b'/' else path + b'/'


def normpath(path: bytes, *, ensure_end_with_sep=False) -> bytes:
    p = _remove_consecutive_duplicates(path).removeprefix(b'/')
    if ensure_end_with_sep:
        return _ensure_end_with_sep(p)
    return p
