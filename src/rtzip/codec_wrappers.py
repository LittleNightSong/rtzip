import sys

from .algorithm_register import algorithm_handler


@algorithm_handler(0x0008)
async def deflate_wrapper(gen, entry, header, ext):
    import zlib
    decompresser = zlib.decompressobj(-15)  # 禁用 zlib 头部
    async for chunk in gen:
        yield decompresser.decompress(chunk)
    remaining = decompresser.flush()
    if remaining:
        yield remaining


@algorithm_handler(0x0000)
async def raw_wrapper(gen, entry, header, ext):
    async for chunk in gen:
        yield chunk


@algorithm_handler(0x000E)
async def lzma_wrapper(gen, entry, header, ext):
    import lzma
    decompressor = lzma.LZMADecompressor()

    async for chunk in gen:
        data = decompressor.decompress(chunk)
        if data:
            yield data

    if not decompressor.eof:
        raise lzma.LZMAError(
            'LZMA compressed data stream ended unexpectedly (EOF not reached) – data may be truncated or corrupted')

    if decompressor.unused_data:
        raise lzma.LZMAError(
            f'LZMA stream contains {len(decompressor.unused_data)} bytes of trailing data after the end marker'
            f' – possibly concatenated streams or extra metadata'
        )

@algorithm_handler(0x000C)
async def bz2_wrapper(gen, entry, header, ext):
    import bz2
    decompressor = bz2.BZ2Decompressor()

    async for chunk in gen:
        if chunk:
            data = decompressor.decompress(chunk)
            if data:
                yield data

    if not decompressor.eof:
        raise ValueError(
            "BZIP2 compressed data stream ended unexpectedly (EOF not reached) – "
            "data may be truncated or corrupted"
        )

    if decompressor.unused_data:
        raise ValueError(
            f"BZIP2 stream contains {len(decompressor.unused_data)} bytes of trailing data after the end marker – "
            f"possibly concatenated streams or extra metadata"
        )


if sys.version_info >= (3, 14):
    @algorithm_handler(0x000D)
    async def zstd_wrapper(gen, entry, header, ext):  # Not Tested
        import warnings
        warnings.warn("ZStandard support is experimental and has not been tested")

        import compression.zstd as zstd
        decompressor = zstd.ZstdDecompressor()
        async for chunk in gen:
            data = decompressor.decompress(chunk)
            if data:
                yield data

        if not decompressor.eof:
            raise ValueError(
                "ZStandard compressed data stream ended unexpectedly (EOF not reached) – "
                "data may be truncated or corrupted"
            )

        if decompressor.unused_data:
            raise ValueError(
                f"ZStandard stream contains {len(decompressor.unused_data)} bytes of trailing data after the end marker – "
                f"possibly concatenated streams or extra metadata"
            )