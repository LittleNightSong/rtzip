import struct
from collections.abc import Buffer
from fnmatch import fnmatch
from typing import AsyncGenerator

from .algorithm_register import is_algorithm_available, get_algorithm_handler
from .const import *
from .errors import UnsupportedAlgorithmError
from .models import EOCD, Zip64EOCD, CDEntry, LocalFileHeader
from .zip_crypto import zipcrypto_wrapper
from .path_methods import normpath


class DataSource:
    async def read_range(self, offset, length) -> bytes | bytearray | memoryview:
        """
        读取指定的一段数据

        :param offset: 数据位置偏移量
        :param length: 在此位置之后读取的数据大小
        :return: bytes | bytearray | memoryview
        """

    def read_range_stream(self, offset, length) -> AsyncGenerator[bytes | bytearray | memoryview, None]:
        """
        流式读取时指定的一段数据

        返回一个异步生成器（或迭代器）

        :param offset: 数据起始偏移量
        :param length: 在此位置之后读取的数据大小
        :return: bytes | bytearray | memoryview
        """

    async def get_total_size(self) -> int:
        """
        获取数据的总大小，如果无法获取或数据大小有误，应抛出异常中断操作
        :return: int，表示数据的总大小
        """


async def single_chunk_wrapper(__data: Buffer, /):
    yield __data


class RemoteZip:
    """
    远程 Zip 文件访问器

    （RemoteZip 不会缓存已读取的文件，你需要自己保证数据复用）

    :ivar source: 数据源回调对象
    :ivar zip_info: 中央仓库元数据（EOCD），可能为 EOCD、Zip64EOCD 或 None 值
    :ivar files: 中央目录文件列表
    :ivar file_mapping: 构建的文件映射表，可能为 None
    :ivar is_zip64: 此 zip 文件是否启动了 zip64 扩展，默认为 False
    """

    async def _read_range(self, offset, length):
        return await self.source.read_range(offset, length)

    def _read_range_stream(self, offset, length):
        return self.source.read_range_stream(offset, length)

    async def _get_total_size(self):
        return await self.source.get_total_size()

    __slots__ = [
        'source', 'zip_info', 'files', 'file_mapping', 'cached_headers', 'is_zip64',
        '_namelist'
    ]

    def __init__(self, source: DataSource):
        self.source = source

        self.zip_info: EOCD | Zip64EOCD | None = None
        self.files: list[CDEntry] | None = None
        self.file_mapping: dict[bytes, CDEntry] | None = None

        self.cached_headers: dict[bytes, LocalFileHeader] = {}

        self.is_zip64 = False
        self._namelist = None

    def namelist(self):
        if self._namelist is None:
            if self.files is None:
                raise ValueError("No files. Please call `fetch_file_list()` first")
            self._namelist = [
                f.filename for f in self.files
            ]

        return self._namelist

    async def fetch_zip_info(
            self,
            initial_chunk=4096,
            max_cnt=-1,
            loss_factor: int | float = 2,
            reuse=True
    ) -> EOCD | Zip64EOCD:
        """
        获取 zip 文件的元信息（EOCD）

        :param reuse: 如果启用，函数会缓存上一次的获取结果（并保存在 ``.zip_info`` 中），否则将会重新扫描 EOCD 并覆盖旧缓存
        :param initial_chunk: 初始扫描的大小
        :param max_cnt: 最大重试次数
        :param loss_factor: 重试后更新扫描大小的系数
        :return: 一个 ``EOCD`` 或者 ``Zip64EOCD`` 对象（如果这个 zip 文件启用了 zip64 扩展）

        """
        if reuse and self.zip_info is not None:
            return self.zip_info

        chunk_size = initial_chunk

        last_start = await self._get_total_size()
        # print("File Size:", last_start)
        cnt = 0

        buffer = b''
        while last_start - chunk_size > 0:
            cnt += 1
            content = await self._read_range(last_start - chunk_size, chunk_size)

            buffer = bytes(content) + buffer[:32]

            signature_pos = buffer.rfind(EOCD_SIGNATURE)
            if signature_pos != -1:
                self.zip_info = eocd = EOCD.from_buffer(buffer[signature_pos:])
                # return cnt
                if eocd.total_entries == 0xFFFFFFFF or eocd.cd_offset == 0xFFFFFFFF or eocd.cd_size == 0xFFFFFFFF:
                    # 处理 zip64 扩展
                    # 我们需要解析 Locator 并定位到 Zip64 EOCD 获取信息
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

                    self.zip_info = zip64_eocd
                    return self.zip_info
                else:
                    return self.zip_info

            if max_cnt != -1 and cnt >= max_cnt:
                raise RuntimeError

            last_start -= chunk_size
            chunk_size = int(chunk_size * loss_factor)
        else:
            raise RuntimeError

    async def fetch_file_list(self, ignore_extra: bool = False, reuse=True) -> list[CDEntry]:
        """
        从远程 zip 的中央目录获取文件列表

        自动在 `zip_info` 缺失的情况下拉取

        :param ignore_extra: 是否忽略无法解析的片段并返回已有的内容
        :param reuse: 如果启用，将自动缓存已拉取的数据（位于 ``.files`` 属性）并复用它，否则将重新拉取文件列表并覆盖缓存
        :return: 一个列表，包含所有获取到的文件
        """

        if reuse and self.files is not None:
            return self.files

        if self.zip_info is None:
            self.zip_info = await self.fetch_zip_info()

        eocd = self.zip_info
        buffer = bytearray()
        files = []

        def consume_entry():
            nonlocal buffer
            entry, nbytes = CDEntry.from_buffer(buffer)

            # print(f'{len(files) + 1:03} Get Entry', entry)

            if self.is_zip64:
                entry.fix_by_zip64()

            files.append(entry)
            buffer = buffer[nbytes + 4:]

        async for chunk in self._read_range_stream(eocd.cd_offset, eocd.cd_size):
            # print("Read Chunk", len(chunk))
            # print(chunk)
            buffer.extend(chunk)

            while buffer and buffer[:4] == CDENTRY_SIGNATURE:
                try:
                    consume_entry()
                except (struct.error, AssertionError):
                    break

        if buffer and not ignore_extra:
            raise ValueError()

        self.files = files

        self._namelist = None

        return files

    async def fetch_file_header(self, filename=None, entry: CDEntry | None = None, reuse=True):
        """
        获取 filename 对应的本地文件头

        :param reuse: 如果启用，将自动缓存已获取的本地文件头，否则将重新拉取并覆盖缓存
        :param filename: 文件名称，可选
        :param entry: CDEntry 对象，可选
        :return: 一个 LocalFileHeader 对象，以及在解析时获取到的多余数据（在文件本身很小的时候，这一段数据可以直接解析出文件内容）
        """

        entry = entry or self.find_file_entry(normpath(bytes(filename)))

        if reuse and entry.filename in self.cached_headers:
            return self.cached_headers[entry.filename]

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

        if entry.has_data_descriptor:
            header.compressed_size = entry.compressed_size
            header.uncompressed_size = entry.original_size
            header.crc32 = entry.crc32_raw

        self.cached_headers[entry.filename] = header

        return header, buffer[pos:]  # 返回剩余的数据部分

    def build_mapping(self):
        """
        构建文件映射表，格式为 filename -> CDEntry

        重复调用将会重建映射表

        :return: 构建完成的映射表
        """
        if self.files is None:
            raise ValueError("No files. Please call `fetch_file_list` first.")

        m = self.file_mapping = {}
        for entry in self.files:
            m[entry.filename] = entry

        return m

    def find_file_entry(self, filename: bytes):
        """
        从本地已有的数据获取 filename 对应的 CDEntry 对象

        如果已有构建好的文件映射，则直接查找文件映射内容
        否则将遍历中央仓库找到文件

        若是发现文件不存在，将会抛出 FileNotFoundError

        :param filename: 文件名
        :raise FileNotFoundError: 当找不到所需要的文件时抛出
        :raise ValueError: 当本地没有文件列表时抛出
        :return: 文件的 CDEntry 对象
        """

        filename = normpath(filename)

        if self.files is None:
            raise ValueError("No files. Please call `fetch_file_list` first.")

        if self.file_mapping:
            return self.file_mapping[filename]
        else:
            for i in self.files:
                if i.filename == filename:
                    return i
            else:
                raise FileNotFoundError(filename)

    async def _stream_single_file(self, filename: bytes, kwargs):
        entry = self.find_file_entry(filename)

        if not is_algorithm_available(entry.algorithm):
            raise UnsupportedAlgorithmError(entry.algorithm)

        header, buffer = await self.fetch_file_header(entry=entry)
        real_data_offset = entry.local_header_offset + header.data_offset

        if entry.has_data_descriptor:
            header.compressed_size = entry.compressed_size
            header.uncompressed_size = entry.original_size
            header.crc32 = entry.crc32_raw

        if entry.compressed_size < len(buffer):
            raw_generator = single_chunk_wrapper(buffer[:entry.compressed_size])
        else:
            raw_generator = self._read_range_stream(real_data_offset, entry.compressed_size)

        if entry.is_encrypted and entry.algorithm != 0x63:  # 暂时只支持 Zip-Crypto (Legacy)
            raw_generator = zipcrypto_wrapper(raw_generator, entry, header, kwargs)

        # 对于 zip-aes 的支持可以作为 algorithm-handler
        # 它会把 algorithm 设成 0x63，可以直接让它路由到我们的 AES 解密包装器上

        return get_algorithm_handler(entry.algorithm)(raw_generator, entry, header, kwargs)

    async def stream(self, filename: bytes, **kwargs):
        """
        流式读取一个压缩包内的文件

        :param filename: 压缩包内的文件名
        :return:
        """
        gen = await self._stream_single_file(normpath(filename), kwargs)
        async for i in gen:
            yield i

    async def read(self, filename: bytes, **kwargs) -> bytearray:
        """
        直接全量加载文件的内容

        建议在小文件上使用此方法

        :param filename: 压缩包内的文件名
        :return: 一个 bytearray 表示文件内容
        """
        buffer = bytearray()
        async for i in self.stream(filename, **kwargs):
            buffer.extend(i)

        return buffer


    async def resolve(self, filename: bytes, max_resolve=24, **kwargs) -> bytes:
        entry = self.find_file_entry(filename)
        target = entry.filename

        cnt = 0
        while entry.is_symlink:
            target = await self.read(filename, **kwargs)
            try:
                entry = self.find_file_entry(target)
            except FileNotFoundError:
                break

            cnt += 1

        return target

    def listdir(self, path: bytes) -> list[bytes]:
        path = normpath(path).removesuffix(b'/') + b'/'
        return [
            i
            for i in self.namelist()
            if i.startswith(path) and i != path  # 确保不会包含目录本身
        ]

    def exists(self, path: bytes) -> bool:
        return normpath(path) in self.namelist()

    def entry(self, path: bytes) -> CDEntry:
        i = self.namelist().index(normpath(path))
        if i == -1:
            raise FileNotFoundError()

        return self.files[i]

    def original_size(self, path: bytes) -> int:
        return self.entry(path).original_size

    def compressed_size(self, path: bytes) -> int:
        return self.entry(path).compressed_size

    async def header(self, path: bytes):
        return await self.fetch_file_header(
            filename=normpath(path)
        )

    async def read_text(self, path: bytes, encoding='utf-8', errors='strict') -> str:
        return (await self.read(path)).decode(encoding, errors=errors)

    def rglob(self, pattern):
        for file in self._namelist:
            if fnmatch(file, pattern):
                yield file

    def rglob_entries(self, pattern):
        if self.files is None:
            raise ValueError("No files. Please call `fetch_file_list` first.")

        for file in self.files:
            if fnmatch(file.filename, pattern):
                yield file
