import unittest

from rtzip import RemoteZip, builtin_sources
from rtzip import Zip64EOCD


class Zip64Test(unittest.IsolatedAsyncioTestCase):
    async def test_offset_overflow(self):
        src = builtin_sources.LocalFileSourceSync('./samples/zip64_offset_overflow.zip')

        async with RemoteZip(src) as rz:
            with self.subTest(msg="Check header offset"):
                self.assertEqual(rz.info(b'over').header_offset, 4294967430, "偏移量识别不正确")
            with self.subTest(msg="Check compress size"):
                self.assertEqual(rz.info(b'offset_overflow-1').compress_size, 2 * 1024 * 1024 * 1024, "文件大小不正确")

    # async def test_too_many_entries(self):  # No samples
    #     src = builtin_sources.LocalFileSourceSync('../samples/zip64_too_many_files.zip')
    #     rz = await RemoteZip.open(src)
    #
    #     # self.assertEqual(rz)

    async def test_one_big_file(self):
        src = builtin_sources.LocalFileSourceSync('./samples/zip64_one_big_file.zip')

        async with RemoteZip(src) as rz:
            self.assertIsInstance(rz.eocd, Zip64EOCD, "没有获取 Zip64EOCD")
            self.assertEqual(rz.original_size(b'big_4gb'), 4 * 1024 * 1024 * 1024)
