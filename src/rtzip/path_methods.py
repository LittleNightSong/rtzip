def remove_consecutive_duplicates(data: bytes, byte: int = b'/'[0]) -> bytes:
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


def normpath(path: bytes) -> bytes:
    return remove_consecutive_duplicates(path).removeprefix(b'/')
