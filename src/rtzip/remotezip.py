import struct
from typing import AsyncGenerator

from .errors import UnsupportedAlgorithmError, UnsupportedCryptoError, UnsupportedDataDescriptorError
from .models import EOCD, Zip64EOCD, CDEntry, LocalFileHeader
from .utils import deflate_wrapper, single_chunk_wrapper

from .const import *


class DataSourceCallbacks:
    async def read_range(self, offset, length) -> bytes | bytearray | memoryview:
        ...

    def read_range_stream(self, offset, length) -> AsyncGenerator[bytes | bytearray | memoryview]:
        ...

    async def get_total_size(self) -> int:
        ...


class RemoteZip:
    async def _read_range(self, offset, length):
        return await self.source.read_range(offset, length)

    def _read_range_stream(self, offset, length):
        return self.source.read_range_stream(offset, length)

    async def _get_total_size(self):
        return await self.source.get_total_size()

    def __init__(self, source: DataSourceCallbacks):
        self.source = source

        self.cd_info: EOCD | Zip64EOCD | None = None
        self.files: list[CDEntry] = []
        self.file_mapping: dict[str, CDEntry] | None = None

        self.is_zip64 = False

    async def fetch_eocd(self, initial_chunk=4096, max_cnt=-1, loss_factor: int | float = 2):
        chunk_size = initial_chunk

        last_start = await self._get_total_size()
        cnt = 0

        buffer = b''
        while last_start - chunk_size > 0:
            cnt += 1
            content = await self._read_range(last_start - chunk_size, chunk_size)

            buffer = bytes(content) + buffer[:32]

            signature_pos = buffer.rfind(EOCD_SIGNATURE)
            if signature_pos != -1:
                self.cd_info = eocd = EOCD.from_buffer(buffer[signature_pos:])
                # return cnt
                if eocd.total_entries == 0xFFFFFFFF or eocd.cd_offset == 0xFFFFFFFF or eocd.cd_size == 0xFFFFFFFF:
                    self.is_zip64 = True

                    # 继续获取 zip64 的 eocd
                    # zip64 locator 在 eocd 前的 20 字节处
                    # 字段包括 4 字节的签名，两个 8 字节数据

                    zip64_locator_offset = signature_pos - 20
                    # 检查需要的数据是否已经存在
                    if zip64_locator_offset < 0:
                        # print("--------------------Zero Padding")
                        buffer = (
                                await self._read_range(
                                    last_start - chunk_size - abs(zip64_locator_offset),
                                    abs(zip64_locator_offset)
                                )
                                + buffer
                        )

                        zip64_locator_offset = 0

                    # print(buffer)

                    assert buffer[zip64_locator_offset:zip64_locator_offset + 4] == ZIP64_EOCD_LOCATOR_SIGNATURE
                    zip64_eocd_disk_id, zip64_eocd_offset, zip64_total_disks = \
                        struct.unpack_from('<4xIQI', buffer, zip64_locator_offset)

                    # print(zip64_eocd_disk_id, zip64_eocd_offset, zip64_total_disks)

                    zip64_eocd_data = await self._read_range(zip64_eocd_offset, 96)
                    # print(zip64_eocd_data[:64])
                    assert zip64_eocd_data[:4] == ZIP64_EOCD_SIGNATURE

                    zip64_eocd = Zip64EOCD.from_buffer(zip64_eocd_data, header_only=True)
                    # print(zip64_eocd)

                    extra_offset = zip64_eocd.extra_offset + 4
                    extra_length = zip64_eocd.extra_length
                    extra_end = extra_offset + extra_length

                    # print(extra_offset, extra_end, buffer[extra_offset:extra_end])

                    if extra_end > len(zip64_eocd_data):
                        zip64_eocd_data += await self._read_range(
                            zip64_eocd_offset + 64,
                            extra_end - len(zip64_eocd_data)
                        )

                    extra_data = zip64_eocd_data[extra_offset:extra_offset + extra_length]

                    zip64_eocd.extra_data = extra_data

                    # print(zip64_eocd)

                    self.cd_info = zip64_eocd
                    return cnt
                else:
                    return cnt

            if max_cnt != -1 and cnt >= max_cnt:
                raise RuntimeError

            last_start -= chunk_size
            chunk_size = int(chunk_size * loss_factor)
        else:
            raise RuntimeError

    async def fetch_cd(self, ignore_extra: bool = False):
        assert self.cd_info
        eocd = self.cd_info
        buffer = bytearray()
        files = []

        def consume_entry():
            nonlocal buffer
            entry, nbytes = CDEntry.from_buffer(buffer)

            if self.is_zip64:
                entry.fix_by_zip64()

            files.append(entry)
            buffer = buffer[nbytes + 4:]

        async for chunk in self._read_range_stream(eocd.cd_offset, eocd.cd_size):
            # print(chunk)
            buffer.extend(chunk)

            while buffer and buffer[:4] == CDENTITY_SIGNATURE:
                try:
                    consume_entry()
                except struct.error:
                    continue

        if buffer and not ignore_extra:
            # print("Read Entries: ", len(files))
            # print("Buffer:", buffer)
            raise ValueError()

        self.files = files

    async def fetch_single_file_header(self, filename=None, entry: CDEntry | None = None):
        filename = str(filename)
        entry = entry or self.find_file_entry(filename)
        buffer = await self._read_range(entry.local_header_offset, entry.__cstruct__.size + 64)  # 预留64字节的变长字段

        # 仅头部模式解析 LocalFileHeader
        header, _ = LocalFileHeader.from_buffer(buffer, 4, header_only=True)
        if header.extra_len + header.filename_len > 64:
            _ = await self._read_range(entry.local_header_offset + 64, header.extra_len + header.filename_len - 64)
            buffer = bytes(buffer) + _

        pos = header.__cstruct__.size + 4  # 4 字节的签名在计算 nbytes 时会被忽略，实际需要加上
        filename = buffer[pos: pos + header.filename_len]
        pos += header.filename_len

        extra_fields = buffer[pos:pos + header.extra_len]
        pos += header.extra_len

        header.filename = bytes(filename)
        header.raw_extra_fields = bytes(extra_fields)

        header.fix_by_zip64()
        return header, buffer[pos:]  # 返回剩余的数据部分

    def build_mapping(self):
        m = self.file_mapping = {}
        for entry in self.files:
            m[entry.filename] = entry

    def find_file_entry(self, filename):
        if self.file_mapping:
            return self.file_mapping[filename]
        else:
            for i in self.files:
                if i.filename == filename:
                    return i
            else:
                raise FileNotFoundError(filename)

    async def _stream_single_file(self, filename):
        entry = self.find_file_entry(filename)

        if entry.is_encrypted:
            raise UnsupportedCryptoError()

        if entry.has_data_descriptor:
            raise UnsupportedDataDescriptorError()

        if entry.algorithm not in {0x0000, 0x0008}:
            raise UnsupportedAlgorithmError(entry.algorithm)

        header, buffer = await self.fetch_single_file_header(entry=entry)
        # print(header)
        real_data_offset = entry.local_header_offset + header.data_offset

        if entry.compressed_size < len(buffer):
            raw_generator = single_chunk_wrapper(buffer[:entry.compressed_size])
        else:
            raw_generator = self._read_range_stream(real_data_offset, entry.compressed_size)

        match entry.algorithm:
            case 0x0000:
                return raw_generator
            case 0x0008:
                return deflate_wrapper(raw_generator)

    async def stream_single_file(self, filename):
        gen = await self._stream_single_file(filename)
        async for i in gen:
            yield i

    async def load_single_file(self, filename) -> bytearray:
        buffer = bytearray()
        async for i in self.stream_single_file(filename):
            buffer.extend(i)

        return buffer
