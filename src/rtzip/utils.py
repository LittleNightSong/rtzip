import zlib
from collections.abc import Buffer


async def deflate_wrapper(gen):
    decompresser = zlib.decompressobj(-15)
    async for chunk in gen:
        yield decompresser.decompress(chunk)
    remaining = decompresser.flush()
    if remaining:
        yield remaining


async def single_chunk_wrapper(data: Buffer):
    yield data


def exactly_get_slice(__obj, __offset, __length):
    result = __obj[__offset:__offset + __length]
    assert len(result) == __length
    return result
