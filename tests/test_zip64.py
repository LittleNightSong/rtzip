import asyncio
import time
import unittest
from rtzip import RemoteZip, builtin_sources


def run(func):
    asyncio.run(func())
    return func


@run
async def test():
    src = builtin_sources.LocalFileSourceSync('../samples/zip64_too_many_files.zip')
    rz = RemoteZip(src)
    eocd = await rz.lowlevel.fetch_eocd()

    print(eocd)

    st = time.perf_counter()

    async for i in rz.lowlevel.iter_fetch_central_directory():
        print(i.filename)

    et = time.perf_counter()

    elapsed = et - st
    print(elapsed)


class Zip64Test(unittest.IsolatedAsyncioTestCase):
    async def test_offset_overflow(self):
        src = builtin_sources.LocalFileSourceSync('./samples/zip64_offset_overflow.zip')
        rz = await RemoteZip.open(src)

        self.assertEqual(rz.entry(b'over').local_header_offset, 4294967430, "偏移量识别不正确")
        self.assertEqual(rz.entry(b'offset_overflow-1').compressed_size, 2 * 1024 * 1024 * 1024)

        await rz.close_source()

    # async def test_too_many_entries(self):
    #     src = builtin_sources.LocalFileSourceSync('../samples/zip64_too_many_files.zip')
    #     rz = await RemoteZip.open(src)
    #
    #     # self.assertEqual(rz)

    async def test_one_big_file(self):
        src = builtin_sources.LocalFileSourceSync('./samples/zip64_one_big_file.zip')

        async with RemoteZip(src) as rz:
            self.assertEqual(rz.original_size(b'big_4gb'), 4 * 1024 * 1024 * 1024)
