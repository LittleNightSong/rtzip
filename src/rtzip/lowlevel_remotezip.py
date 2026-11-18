import struct

from .algorithm_register import is_algorithm_handler_available, get_algorithm_handler
from .const import *
from .context import Context
from .errors import InvalidOffsetError, BadZipError, NotEnoughDataError, UnsupportedAlgorithmError
from .structs import *
from .protocols import DataSource


async def _yield_single[T](__data: T):
    yield __data


class LowLevelRemoteZip:
    async def _read_range(self, offset, length):
        if self._total_size is not None and offset + length > self._total_size:
            raise InvalidOffsetError(offset, length, self._total_size)

        return await self.source.read_range(offset, length)

    def _read_range_stream(self, offset, length):
        if self._total_size is not None and offset + length > self._total_size:
            raise InvalidOffsetError(offset, length, self._total_size)

        return self.source.read_range_stream(offset, length)

    async def _get_total_size(self):
        if self._total_size is None:
            self._total_size = await self.source.get_total_size()

        return self._total_size

    __slots__ = [
        'source', 'eocd', 'central_directory', 'file_mapping', 'headers_cache',
        'context',
        '_is_zip64', '_total_size'
    ]

    def __init__(self, source: DataSource, context: Context):
        self.source = source
        self.context = context

        self.eocd: EOCD | Zip64EOCD | None = None
        self.central_directory: list[CDEntry] | None = None
        self.file_mapping: dict[bytes, CDEntry] | None = None
        self.headers_cache: dict[bytes, LocalFileHeader] = {}

        self._is_zip64 = None
        self._total_size = None

    async def fetch_eocd(
            self
    ) -> EOCD | Zip64EOCD:
        """
        获取 zip 文件的元信息（EOCD）

        :return: 一个 ``EOCD`` 或者 ``Zip64EOCD`` 对象（如果这个 zip 文件启用了 zip64 扩展）

        """
        ctx = self.context

        if ctx.eocd_scan_use_cache and self.eocd is not None:
            return self.eocd

        chunk_size = ctx.eocd_scan_initial_chunk_size

        last_start = await self._get_total_size()
        # print("File Size:", last_start)
        cnt = 0

        buffer = bytearray()
        while last_start - chunk_size > 0:
            cnt += 1
            content = await self._read_range(last_start - chunk_size, chunk_size)

            _ = buffer
            buffer = bytearray(content)
            buffer.extend(_[:30])

            signature_pos = buffer.rfind(EOCD_SIGNATURE)
            if signature_pos != -1:
                self.eocd = eocd = EOCD.from_buffer(buffer[signature_pos:])
                # return cnt
                if eocd.total_entries == 0xFFFFFFFF or eocd.cd_offset == 0xFFFFFFFF or eocd.cd_size == 0xFFFFFFFF:
                    # 处理 zip64 扩展
                    # 我们需要解析 Locator 并定位到 Zip64 EOCD 获取信息
                    self._is_zip64 = True

                    # 继续获取 zip64 的 eocd
                    # zip64 locator 在 eocd 前的 20 字节处
                    # 字段包括 4 字节的签名，两个 8 字节数据

                    zip64_locator_offset = signature_pos - 20
                    # 检查需要的数据是否已经存在
                    if zip64_locator_offset < 0:
                        # print("--------------------Zero Padding")
                        data_before = await self._read_range(
                            last_start - chunk_size - abs(zip64_locator_offset),
                            abs(zip64_locator_offset)
                        )

                        _ = bytearray(data_before)
                        _.extend(buffer)

                        buffer = _

                        zip64_locator_offset = 0

                    # print(buffer)

                    # assert buffer[zip64_locator_offset:zip64_locator_offset + 4] == ZIP64_EOCD_LOCATOR_SIGNATURE
                    if buffer[zip64_locator_offset: zip64_locator_offset + 4] != ZIP64_EOCD_LOCATOR_SIGNATURE:
                        raise BadZipError('Wrong zip64 EOCD locator signature')

                    zip64_eocd_disk_id, zip64_eocd_offset, zip64_total_disks = \
                        struct.unpack_from('<4xIQI', buffer, zip64_locator_offset)

                    # print(zip64_eocd_disk_id, zip64_eocd_offset, zip64_total_disks)

                    zip64_eocd_data = bytes(await self._read_range(zip64_eocd_offset, 96))

                    if zip64_eocd_data[:4] != ZIP64_EOCD_SIGNATURE:
                        raise BadZipError('Wrong zip64 EOCD signature')

                    zip64_eocd = Zip64EOCD.from_buffer(zip64_eocd_data)
                    # print(zip64_eocd)

                    extra_offset = zip64_eocd.header_size + 4
                    extra_length = zip64_eocd.extra_length
                    extra_end = extra_offset + extra_length

                    # print(extra_offset, extra_end, buffer[extra_offset:extra_end])

                    if extra_end > len(zip64_eocd_data):
                        zip64_eocd_data += await self._read_range(
                            zip64_eocd_offset + 64,
                            extra_end - len(zip64_eocd_data)
                        )

                    zip64_eocd.fix_missing(zip64_eocd_data, extra_offset)

                    # print(zip64_eocd)

                    self.eocd = zip64_eocd
                    return self.eocd
                else:
                    return self.eocd

            if ctx.eocd_scan_max_retry != -1 and cnt >= ctx.eocd_scan_max_retry:
                raise RuntimeError

            last_start -= chunk_size
            chunk_size = int(chunk_size * ctx.eocd_scan_rechunk_factor)
        else:
            raise RuntimeError

    async def iter_fetch_central_directory(self, ignore_extra: bool = False):
        if self.eocd is None:
            raise ValueError("No EOCD fetched.")
        eocd = self.eocd
        buffer = bytearray()

        def consume_entry():
            entry = CDEntry.from_buffer(buffer)

            # print(f'{len(files) + 1:03} Get Entry', entry)

            if self._is_zip64:
                entry.fix_zip64()

            del buffer[:entry.total_size]

            return entry

        async for chunk in self._read_range_stream(eocd.cd_offset, eocd.cd_size):
            # print("Read Chunk", len(chunk))
            # print(chunk)
            buffer.extend(chunk)

            while len(buffer) > 46 and buffer[:4] == CDENTRY_SIGNATURE:
                try:
                    yield consume_entry()
                except NotEnoughDataError:
                    break

        if buffer and not ignore_extra:
            raise BadZipError('Extra data at end of the central directory entries.')

    async def fetch_central_directory(self, ignore_extra: bool = False, reuse=True) -> list[CDEntry]:
        """
        从远程 zip 的中央目录流式获取文件列表

        自动在 `zip_info` 缺失的情况下拉取

        :param ignore_extra: 是否忽略无法解析的片段并返回已有的内容
        :param reuse: 如果启用，将自动缓存已拉取的数据（位于 ``.files`` 属性）并复用它，否则将重新拉取文件列表并覆盖缓存
        :return: 一个列表，包含所有获取到的文件
        """

        if reuse and self.central_directory is not None:
            return self.central_directory

        self.central_directory = files = [
            i async for i in self.iter_fetch_central_directory(ignore_extra=ignore_extra)]
        return files

    async def fetch_local_file_header(self, filename=None, entry: CDEntry | None = None, reuse=True) -> tuple[
        LocalFileHeader, bytes | memoryview | bytearray]:
        """
        获取 filename 对应的本地文件头

        :param reuse: 如果启用，将自动缓存已获取的本地文件头，否则将重新拉取并覆盖缓存
        :param filename: 文件名称，可选
        :param entry: CDEntry 对象，可选
        :return: 一个 LocalFileHeader 对象，以及在解析时获取到的多余数据（在文件本身很小的时候，这一段数据可以直接解析出文件内容）
        """
        if entry is None and filename is None:
            raise ValueError("None of infomation of the file is given.")

        entry = entry or self.find_file_entry(filename)

        if reuse and entry.filename in self.headers_cache:
            return self.headers_cache[entry.filename], b''

        buffer = await self._read_range(entry.local_header_offset, entry.local_header_offset + 94)  # 预留64字节的变长字段

        if buffer[:4] != LFH_SIGNATURE:
            raise BadZipError('Bad Local File Header signature')

        # 仅头部模式解析 LocalFileHeader
        header = LocalFileHeader.from_buffer(buffer, 4)
        # print("Dict", dir(header))

        if header.total_size > 94:  #
            _ = await self._read_range(entry.local_header_offset + 64, header.extra_len + header.filename_len - 64)
            buffer = bytes(buffer) + _

        header.fix_missing(buffer, 30)
        # print(header)
        header.fix_zip64()

        if entry.has_data_descriptor:
            header.compress_size = entry.compress_size
            header.file_size = entry.original_size
            header.crc32 = entry.crc32

        self.headers_cache[entry.filename] = header

        return header, buffer[header.total_size:]  # 返回剩余的数据部分

    async def stream_single_file(self, entry, context: Context | None = None):
        if context is not None:
            context = self.context | context
        else:
            context = self.context

        if not is_algorithm_handler_available(entry.algorithm):
            raise UnsupportedAlgorithmError(entry.algorithm)

        header, buffer = await self.fetch_local_file_header(entry=entry)
        real_data_offset = entry.local_header_offset + header.total_size

        if entry.has_data_descriptor:
            header.compress_size = entry.compress_size
            header.file_size = entry.file_size
            header.crc32 = entry.crc32

        if entry.compress_size < len(buffer):
            raw_generator = _yield_single(buffer[:entry.compress_size])
        else:
            raw_generator = self._read_range_stream(real_data_offset, entry.compress_size)

        if entry.is_encrypted and entry.algorithm != 0x63:  # 处理 Zip Crypto 加密方式
            from rtzip.zip_crypto import zipcrypto_wrapper
            raw_generator = zipcrypto_wrapper(raw_generator, entry, header, context)

        return get_algorithm_handler(entry.algorithm)(raw_generator, entry, header, context)

    def build_mapping(self):
        """
        构建文件映射表，格式为 filename -> CDEntry

        重复调用将会重建映射表

        :return: 构建完成的映射表
        """
        if self.central_directory is None:
            raise ValueError("No files. Please call `fetch_file_list` first.")

        m = self.file_mapping = {}
        for entry in self.central_directory:
            m[entry.filename] = entry

        return m

    def find_file_entry(self, filename: bytes):
        """
        从本地已有的数据获取 filename 对应的 CDEntry 对象

        如果已有构建好的文件映射，则直接查找文件映射内容
        否则将遍历中央目录找到文件

        若是发现文件不存在，将会抛出 FileNotFoundError

        :param filename: 文件名
        :raise FileNotFoundError: 当找不到所需要的文件时抛出
        :raise ValueError: 当本地没有文件列表时抛出
        :return: 文件的 CDEntry 对象
        """

        if self.central_directory is None:
            raise ValueError("No files. Please call `fetch_file_list` first.")

        if self.file_mapping:
            return self.file_mapping[filename]
        else:
            for i in self.central_directory:
                if i.filename == filename:
                    return i
            else:
                raise FileNotFoundError(filename)
