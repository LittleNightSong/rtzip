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
