from .const import *
from .errors import BadZipError
from .protocols import DataSource
from .stream_io import StreamIO
from .structs import *


class LowLevelRemoteZip:
    async def _read_range(self, offset, length):
        return await self.source.read_range(offset, length)

    def _read_range_stream(self, offset, length):
        return self.source.read_range_stream(offset, length)

    __slots__ = ['source']

    def __init__(self, source: DataSource):
        self.source = source

    async def parse_eocd_at(self, offset: int):
        data = await self._read_range(offset, 34)
        if data[:4] != EOCD_SIGNATURE:
            raise BadZipError("Wrong EOCD signature")

        eocd = EOCD.from_buffer(data)
        return eocd

    async def scan_eocd(
            self, initial_chunk_size: int = 256, max_retry: int = -1, retry_factor: int | float = 1
    ) -> EOCD | Zip64EOCD:
        """
        获取 zip 文件的元信息（EOCD）

        :return: 一个 ``EOCD`` 或者 ``Zip64EOCD`` 对象（如果这个 zip 文件启用了 zip64 扩展）

        """

        chunk_size = initial_chunk_size
        offset = await self.source.get_total_size()
        cnt = 0

        buffer = bytearray()
        while offset:
            cnt += 1

            read_len = min(offset, chunk_size)
            content = await self._read_range(offset - read_len, read_len)

            offset -= read_len

            _ = buffer
            buffer = bytearray(content)
            buffer.extend(_[:30])

            signature_pos = buffer.rfind(EOCD_SIGNATURE)
            if signature_pos != -1:
                eocd = EOCD.from_buffer(buffer, signature_pos + 4)
                # return cnt
                if eocd.total_entries == 0xFFFFFFFF or eocd.cd_offset == 0xFFFFFFFF or eocd.cd_size == 0xFFFFFFFF:
                    # 处理 zip64 扩展
                    # 我们需要解析 Locator 并定位到 Zip64 EOCD 获取信息
                    # zip64 locator 在 eocd 前的 20 字节处
                    # 字段包括 4 字节的签名，两个 8 字节数据

                    zip64_locator_offset = signature_pos - 20
                    # 检查需要的数据是否已经存在
                    if zip64_locator_offset < 0:
                        # print("--------------------Zero Padding")
                        data_before = await self._read_range(
                            offset - chunk_size - abs(zip64_locator_offset),
                            abs(zip64_locator_offset)
                        )

                        _ = bytearray(data_before)
                        _.extend(buffer)

                        buffer = _

                        zip64_locator_offset = 0

                    if buffer[zip64_locator_offset: zip64_locator_offset + 4] != ZIP64_EOCD_LOCATOR_SIGNATURE:
                        raise BadZipError('Wrong zip64 EOCD locator signature')

                    zip64_eocd_disk_id, zip64_eocd_offset, zip64_total_disks = \
                        struct.unpack_from('<4xIQI', buffer, zip64_locator_offset)

                    # print(zip64_eocd_disk_id, zip64_eocd_offset, zip64_total_disks)

                    zip64_eocd_data = bytearray(await self._read_range(zip64_eocd_offset, 96))

                    if zip64_eocd_data[:4] != ZIP64_EOCD_SIGNATURE:
                        raise BadZipError('Wrong zip64 EOCD signature')

                    zip64_eocd = Zip64EOCD.from_buffer(zip64_eocd_data)
                    # print(zip64_eocd)

                    extra_offset = zip64_eocd.header_size + 4  # 加上 4 字节的签名长度
                    extra_length = zip64_eocd.extra_length
                    extra_end = extra_offset + extra_length

                    # print(extra_offset, extra_end, buffer[extra_offset:extra_end])

                    if extra_end > len(zip64_eocd_data):
                        zip64_eocd_data.extend(await self._read_range(
                            zip64_eocd_offset + 64,
                            extra_end - len(zip64_eocd_data)
                        ))

                    zip64_eocd.fix_missing(zip64_eocd_data, extra_offset)

                    return zip64_eocd
                else:
                    return eocd

            if max_retry != -1 and cnt >= max_retry:
                raise RuntimeError(f"Failed to find a EOCD after {cnt} retries")

            offset -= chunk_size
            chunk_size = int(chunk_size * retry_factor)
        else:
            raise RuntimeError(f"Failed to find a EOCD after {cnt} retries")

    async def parse_central_directory_in(self, offset: int, size: int, fix_zip64: bool = True):
        io = StreamIO(self._read_range_stream(offset, size))

        while True:
            # 读取一个 CDEntry 头
            try:
                header_data = await io.read_exactly(46)

                if header_data[:4] != CDENTRY_SIGNATURE:
                    raise BadZipError('Bad Central Directory Entry signature')

                entry = CDEntry.from_buffer(header_data)

                data_after = await io.read_exactly(entry.filename_len + entry.extra_len)

                entry.fix_missing(data_after, 0)

                if fix_zip64:
                    entry.fix_zip64()

                yield entry

            except EOFError:
                break

    async def list_central_directory_in(self, offset: int, size: int) -> list[CDEntry]:
        """
        从远程 zip 的中央目录流式获取文件列表
        """

        files = [
            i async for i in self.parse_central_directory_in(offset, size)]

        return files

    async def parse_header_at(self, offset: int, fix_zip64: bool = True) -> \
            tuple[LocalFileHeader, bytes | memoryview | bytearray]:
        """
        获取位于 offset 的本地文件头
        """

        buffer = await self._read_range(offset, 94)  # 30 + 64: 预留64字节的变长字段

        if buffer[:4] != LFH_SIGNATURE:
            raise BadZipError('Bad Local File Header signature')

        # 仅头部模式解析 LocalFileHeader
        header = LocalFileHeader.from_buffer(buffer, 4)
        # print("Dict", dir(header))

        if header.total_size > 94:
            _ = await self._read_range(offset + 64, header.extra_len + header.filename_len - 64)
            buffer = bytes(buffer) + _

        header.fix_missing(buffer, 30)

        if fix_zip64:
            header.fix_zip64()

        # print(header)
        return header, buffer[header.total_size:]  # 返回剩余的数据部分
