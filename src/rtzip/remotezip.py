import dataclasses

from rtzip.const import ZIP64_EOCD_LOCATOR_SIGNATURE
import struct
import typing
from fnmatch import fnmatch
from typing import AsyncGenerator, Any

from .algorithm_register import is_algorithm_handler_available, get_algorithm_handler, AlgorithmHandler
from .const import *
from .context import Context
from .errors import UnsupportedAlgorithmError, BadZipError, InvalidOffsetError, NotEnoughDataError
from .models import EOCD, Zip64EOCD, CDEntry, LocalFileHeader
from .path_methods import normpath
from .zip_crypto import zipcrypto_wrapper

empty_context = Context()


class DataSource(typing.Protocol):
    async def read_range(self, offset, length, /) -> bytes | bytearray | memoryview:
        """
        读取指定的一段数据

        :param offset: 数据位置偏移量
        :param length: 在此位置之后读取的数据大小
        :return: bytes | bytearray | memoryview
        """

    def read_range_stream(self, offset, length, /) -> AsyncGenerator[bytes | bytearray | memoryview, None]:
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

    async def setup(self):
        ...

    async def close(self):
        ...


async def single_chunk_wrapper[T](__data: T, /):
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
                entry.fix_by_zip64()

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

    async def fetch_local_file_header(self, filename=None, entry: CDEntry | None = None, reuse=True):
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
            raise BadZipError('Wrong Local File Header signature')

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
            header.compressed_size = entry.compressed_size
            header.uncompressed_size = entry.original_size
            header.crc32 = entry.crc32_raw

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
            header.compressed_size = entry.compressed_size
            header.uncompressed_size = entry.original_size
            header.crc32 = entry.crc32_raw

        if entry.compressed_size < len(buffer):
            raw_generator = single_chunk_wrapper(buffer[:entry.compressed_size])
        else:
            raw_generator = self._read_range_stream(real_data_offset, entry.compressed_size)

        if entry.is_encrypted and entry.algorithm != 0x63:  # 处理 Zip Crypto 加密方式
            from rtzip.zip_crypto import zipcrypto_wrapper
            raw_generator = zipcrypto_wrapper(raw_generator, entry, header, context)

        # 对于 zip-aes 的支持可以作为 algorithm-handler
        # 它会把 algorithm 设成 0x63，可以直接让它路由到我们的 AES 解密包装器上

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


class RemoteZip:
    """
    远程 Zip 文件访问器

    （RemoteZip 不会缓存已读取的文件，你需要自己保证数据复用）

    """
    __slots__ = ('_low', '_namelist', '_namemapping', '_lowmapping', '_entries')

    def __init__(
            self,
            source: DataSource,
            context: Context | None = None,
    ):
        self._low = LowLevelRemoteZip(source, context or Context())

        self._namelist: list[bytes] | None = None
        self._namemapping: dict[bytes, CDEntry] | None = None
        self._lowmapping: dict[bytes, CDEntry] | None = None
        self._entries: list[CDEntry] | None = None

    @property
    def lowlevel(self):
        return self._low

    @property
    def source(self) -> DataSource:
        return self._low.source

    @property
    def entries(self) -> list[CDEntry]:
        if self._entries is None:
            if self._low.central_directory is None:
                raise ValueError("Central directory is not fetched. Please call `init()` first")

            # self._entries = [
            #     dataclasses.replace(entry, filename=(x := normpath(entry.filename)), filename_len=len(x))
            #     for entry in self._low.central_directory
            # ]

            entries = []
            lowmapping = {}
            for e in self._low.central_directory:
                norm_filename = normpath(e.filename)
                entries.append(CDEntry(
                    version=e.version,
                    unzip_min_version=e.unzip_min_version,
                    flags=e.flags,
                    algorithm=e.algorithm,
                    last_modified=e.last_modified,
                    last_modified_date=e.last_modified_date,
                    crc32_raw=e.crc32_raw,
                    compressed_size=e.compressed_size,
                    original_size=e.original_size,
                    filename_len=(len(norm_filename)),
                    extra_len=e.extra_len,
                    comment_len=e.comment_len,
                    disk_id=e.disk_id,
                    internal_file_attrs=e.internal_file_attrs,
                    external_file_attrs=e.external_file_attrs,
                    local_header_offset=e.local_header_offset,
                    filename=norm_filename,
                    raw_extra_fields=e.raw_extra_fields,
                    comment=e.comment,
                ))
                lowmapping[norm_filename] = e

            self._lowmapping = lowmapping
            self._entries = entries

        return self._entries  # type: ignore

    @property
    def namelist(self) -> list[bytes]:
        """
        获取所有的文件名（bytes）列表
        :return: 文件名列表
        """
        if self._namelist is None:
            self._namelist = [i.filename for i in self.entries]

        return self._namelist  # type: ignore

    @property
    def namemapping(self) -> dict[bytes, CDEntry]:
        if self._namemapping is None:
            self._namemapping = {
                f.filename: f
                for f in self.entries
            }

        return self._namemapping  # type: ignore

    @property
    def lowlevel_mapping(self) -> dict[bytes, CDEntry]:
        if self._lowmapping is None:
            self.entries

        return self._lowmapping

    @property
    def total_files(self):
        return len(self.entries)

    def compute_total_size(self):
        if self._low.central_directory is None:
            raise ValueError("Central directory is not fetched. Please call `init()` first")

        return sum(
            i.original_size for i in self.entries
        )

    async def init(self) -> None:
        """
        初始化和拉取基本的元数据（EOCD 和中央仓库）
        :return:
        """
        await self._low.fetch_eocd()
        await self._low.fetch_central_directory()
        await self.setup_source()

    async def close_source(self):
        await self.source.close()

    async def setup_source(self):
        await self.source.setup()

    async def __aenter__(self):
        await self.init()
        return self

    async def __aexit__(self, exc_type, exc, tb):
        await self.close_source()

    @classmethod
    async def open(cls, src: DataSource, ctx: Context | None = None) -> 'RemoteZip':
        """
        打开一个数据源中的 zip 文件
        :param src: 数据源
        :param ctx: 上下文信息
        :return: 一个 RemoteZip 对象
        """
        self = cls(src, ctx)
        await self.init()
        return self

    async def stream(self, filename: bytes, context: Context | None = None):
        """
        流式读取一个压缩包内的文件

        :param filename: 压缩包内的文件名
        :param context: 上下文信息，其中的设置会自动覆盖已有的设置
        :return: 一个字节流异步生成器
        """

        context = (self._low.context | context) if context else Context()

        gen = await self._low.stream_single_file(self.entry(filename), context)
        async for i in gen:
            yield i

    async def read(self, filename: bytes, context: Context | None = None) -> bytearray:
        """
        全量加载文件的内容（底层仍然调用 .stream 方法）

        建议在小文件上使用此方法

        :param filename: 压缩包内的文件名
        :param context: 上下文信息，其中的设置会自动覆盖已有的设置
        :return: 一个 bytearray 表示文件内容
        """

        context = (self._low.context | context) if context else Context()

        buffer = bytearray()
        async for i in self.stream(filename, context):
            buffer.extend(i)

        return buffer

    def listdir(self, path: bytes) -> list[bytes]:
        """
        获取目录下的所有文件名
        :param path: 目录
        :return: 目录下的所有文件名
        """
        path = normpath(path, ensure_end_with_sep=True)
        return [
            i
            for i in self.namelist
            if i.startswith(path) and i != path  # 确保不会包含目录本身
        ]

    def listdir_entries(self, path: bytes) -> list[CDEntry]:
        """
        枚举该目录下所有文件的 CDEntry 对象
        :param path: 目录路径
        :return: 目录下所有文件的 CDEntry 对象列表
        """
        path = normpath(path, ensure_end_with_sep=True)
        return [
            i
            for i in self.entries
            if i.filename.startswith(path) and i.filename != path
        ]

    def exists(self, path: bytes) -> bool:
        """
        判断一个文件（或目录）是否存在
        :param path: 文件名或目录名
        :return: 文件的存在性
        """
        return normpath(path) in self.namemapping

    def entry(self, filename: bytes, original: bool = False) -> CDEntry:
        """
        获取名称对应的 CDEntry 对象
        :param filename: 文件名
        :return: 一个 CDEntry 对象
        """
        mapping = self.namemapping if not original else self.lowlevel_mapping
        try:
            return mapping[normpath(filename)]
        except KeyError:
            raise FileNotFoundError(filename) from None

    def original_size(self, filename: bytes) -> int:
        """
        获取文件的原始大小（解压后大小）
        :param filename: 文件名
        :return: 文件的原始大小
        """
        return self.entry(filename).original_size

    def compressed_size(self, filename: bytes) -> int:
        """
        获取文件的压缩后大小
        :param filename: 文件名
        :return: 文件的压缩后大小
        """
        return self.entry(filename).compressed_size

    def header_offset(self, filename: bytes) -> int:
        """
        获取文件的本地文件头起始偏移量
        :param filename: 文件名
        :return: 文件的本地文件头的起始偏移量
        """
        return self.entry(filename).local_header_offset

    def content_range(self, filename: bytes) -> tuple[int, int]:
        """
        获取文件内容的区间（start, length)
        :param filename: 文件名
        :return: 文件内容的起始偏移量
        """
        entry = self.entry(filename, original=True)
        start = entry.local_header_offset + entry.predicted_lfh_length  # Signature(4) + Header(26)
        length = entry.compressed_size
        return start, length

    def range_and_algorithm(self, filename: bytes) -> tuple[int, int, int]:
        """
        获取文件内容的区间（start, length）和该文件使用的压缩算法标识
        :param filename: 文件名
        :return: 一个三元组，包含文件内容区间和压缩算法标识
        """
        entry = self.entry(filename, original=True)
        return entry.local_header_offset + entry.predicted_lfh_length, entry.compressed_size, entry.algorithm

    def range_and_handler(self, filename: bytes, context: Context | None = None) -> tuple[
        int, int, typing.Callable[[AsyncGenerator[bytes | memoryview | bytearray, None]], AsyncGenerator[
            bytes | memoryview | bytearray, None]]]:
        """
        获取一个文件的内容区间和所需要的处理器
        :param filename: 文件名
        :return: 文件的内容区间和所需要的处理器
        """
        entry = self.entry(filename, original=True)

        offset = entry.local_header_offset + entry.predicted_lfh_length

        if not is_algorithm_handler_available(entry.algorithm):
            raise UnsupportedAlgorithmError(entry.algorithm)

        ctx = self._low.context if context is None else (self._low.context | context)

        # 处理 ZipCrypto
        if entry.is_encrypted and entry.algorithm != 0x63:  # ZipCrypto
            from .zip_crypto import zipcrypto_wrapper

            async def handler(raw_data):
                header, _ = await self._low.fetch_local_file_header(entry=entry)

                gen = get_algorithm_handler(entry.algorithm)(
                    zipcrypto_wrapper(raw_data, entry, header, ctx),
                    entry, header, ctx
                )
                async for i in gen:
                    yield i

        else:
            _handler = get_algorithm_handler(entry.algorithm)

            async def handler(raw_data):
                header, _ = await self._low.fetch_local_file_header(entry=entry)

                gen = get_algorithm_handler(entry.algorithm)(raw_data, entry, header, ctx)
                async for i in gen:
                    yield i

        return offset, entry.compressed_size, handler

    async def header(self, filename: bytes) -> LocalFileHeader:
        """
        拉取一个文件的本地文件头
        :param filename: 文件名
        :return: 文件的本地文件头
        """
        return (await self._low.fetch_local_file_header(entry=self.entry(filename)))[0]

    async def read_text(self, filename: bytes, encoding='utf-8', errors='strict',
                        context: Context | None = None) -> str:
        """
        读取一个文本文件
        :param filename: 文件名
        :param encoding: 文件编码
        :param errors: 错误处理方式
        :param context: 上下文信息
        :return: 文件的文本内容
        """
        return (await self.read(filename, context)).decode(encoding, errors=errors)

    def rglob(self, pattern: bytes) -> typing.Generator[bytes, None, None]:
        """
        递归模式匹配文件

        :param pattern: 模式
        :return: 匹配的文件的生成器
        """
        for file in self.namelist:
            if fnmatch(file, pattern):
                yield file

    def rglob_entries(self, pattern: bytes) -> typing.Generator[CDEntry, None, None]:
        """
        递归模式匹配文件，返回匹配到的文件的 CDEntry 对象
        :param pattern: 模式
        :return: 匹配到的文件的 CDEntry 对象生成器
        """
        for filename, cd in self.namemapping.items():
            if fnmatch(filename, pattern):
                yield cd
