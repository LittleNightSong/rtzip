import dataclasses
import types
import typing
from fnmatch import fnmatch
from typing import AsyncGenerator

from . import structs as _structs
from .algorithm_register import is_algorithm_handler_available, get_algorithm_handler
from .context import Context
from .errors import UnsupportedAlgorithmError, BadZipError
from .lowlevel_remotezip import LowLevelRemoteZip
from .path_methods import normpath
from .protocols import DataSource
from .structs import CDEntry, LocalFileHeader

empty_context = Context()


def check_header_and_entry(header: LocalFileHeader, entry: CDEntry, /):
    # 重点比对：文件名、各种大小、校验值

    cond = (
            header.filename == entry.filename
            and header.compress_size == entry.compress_size
            and header.file_size == entry.original_size
            and header.crc32 == entry.crc32
            and header.algorithm == entry.algorithm
    )

    if not cond:
        raise BadZipError('Validating local file header with central directory entry failed.')


@dataclasses.dataclass(slots=True, kw_only=True, frozen=True)
class FileInfo:
    filename: bytes
    file_size: int
    compress_size: int
    algorithm: int

    # last_modified: datetime.datetime

    volumn: int
    crc: int

    internal_attrs: int
    external_attrs: int
    original_extra: bytes

    create_version: int
    extract_version: int

    header_offset: int

    flags: int

    _entry: CDEntry | None = None
    _parsed_extra: dict[int, bytes] | None = None

    @classmethod
    def from_entry(cls, e: CDEntry, *, parse_extra: bool = False) -> 'FileInfo':
        return cls(
            filename=e.filename,
            file_size=e.original_size,
            compress_size=e.compress_size,
            algorithm=e.algorithm,

            # last_modified=e.last_modified_datetime,

            volumn=e.disk_id,
            crc=e.crc32,

            internal_attrs=e.internal_file_attrs,
            external_attrs=e.external_file_attrs,

            original_extra=e.raw_extra_fields,

            create_version=e.version,
            extract_version=e.unzip_min_version,

            header_offset=e.local_header_offset,

            flags=e.flags,

            _entry=e, _parsed_extra=e.extra_fields if parse_extra else None
        )


class RemoteZip:
    """
    远程 Zip 文件访问器

    （RemoteZip 不会缓存已读取的文件，你需要自己保证数据复用）

    """
    __slots__ = ('_low', '_namelist', '_mapping', '_entries')

    structs = _structs

    def __init__(
            self,
            source: DataSource,
            context: Context | None = None,
    ):
        self._low = LowLevelRemoteZip(source, context or Context())

        self._namelist: list[bytes] | None = None
        self._mapping: dict[bytes, FileInfo] | None = None
        self._entries: list[FileInfo] | None = None

    @property
    def lowlevel(self):
        return self._low

    @property
    def source(self) -> DataSource:
        return self._low.source

    @property
    def context(self):
        return self._low.context

    @property
    def infolist(self) -> list[FileInfo]:
        if self._entries is None:
            if self._low.central_directory is None:
                raise ValueError("Central directory is not fetched. Please call `init()` first")

            self._entries = [
                FileInfo.from_entry(e)
                for e in self._low.central_directory
            ]

        return self._entries  # type: ignore

    @property
    def namelist(self) -> list[bytes]:
        """
        获取所有的文件名（bytes）列表
        :return: 文件名列表
        """
        if self._namelist is None:
            self._namelist = [i.filename for i in self.infolist]

        return self._namelist  # type: ignore

    @property
    def infomapping(self) -> dict[bytes, FileInfo]:
        if self._mapping is None:
            self._mapping = {
                i.filename: i
                for i in self.infolist
            }

        return self._mapping  # type:ignore

    @property
    def total_files(self):
        return len(self.infolist)

    def compute_total_size(self):
        if self._low.central_directory is None:
            raise ValueError("Central directory is not fetched. Please call `init()` first")

        return sum(
            i.file_size for i in self.infolist
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
        x = self.range_and_handler(filename, context)
        if isinstance(x, types.CoroutineType):
            start, length, handler = await x
        else:
            start, length, handler = x


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

    def listdir_info(self, path: bytes) -> list[FileInfo]:
        """
        枚举该目录下所有文件的 CDEntry 对象
        :param path: 目录路径
        :return: 目录下所有文件的 CDEntry 对象列表
        """
        path = normpath(path, ensure_end_with_sep=True)
        return [
            i
            for i in self.infolist
            if i.filename.startswith(path) and i.filename != path
        ]

    def exists(self, path: bytes) -> bool:
        """
        判断一个文件（或目录）是否存在
        :param path: 文件名或目录名
        :return: 文件的存在性
        """
        return normpath(path) in self.infomapping

    def info(self, filename: bytes) -> FileInfo:
        """
        获取文件的 ``FileInfo`` 对象
        :param filename: 文件名
        :return: 一个 ``FileInfo`` 对象
        """

        return self.infomapping[normpath(filename)]

    def original_size(self, filename: bytes) -> int:
        """
        获取文件的原始大小（解压后大小）
        :param filename: 文件名
        :return: 文件的原始大小
        """
        return self.info(filename).file_size

    def compressed_size(self, filename: bytes) -> int:
        """
        获取文件的压缩后大小
        :param filename: 文件名
        :return: 文件的压缩后大小
        """
        return self.info(filename).compress_size

    def header_offset(self, filename: bytes) -> int:
        """
        获取文件的本地文件头起始偏移量
        :param filename: 文件名
        :return: 文件的本地文件头的起始偏移量
        """
        return self.info(filename).header_offset

    def predicted_content_range(self, filename: bytes) -> tuple[int, int]:
        """
        获取文件内容的区间（start, length)
        :param filename: 文件名
        :return: 文件内容的起始偏移量和长度
        """
        entry = self.info(filename)._entry
        if entry is None:
            raise ValueError(f"Bad entry object for {filename!r}")

        start = entry.local_header_offset + entry.predicted_lfh_size  # Signature(4) + Header(26)
        length = entry.compress_size
        return start, length

    def content_range(self, filename: bytes, context: Context | None = None) -> tuple[int, int] | typing.Coroutine[
        typing.Any, typing.Any, tuple[int, int]]:
        """
        获取文件内容的区间

        如果指定了 ``context.read_dont_fetch_header`` 函数会立即返回区间二元组，
        否则函数将返回一个 ``Coroutine``，你需要等待它以获取区间二元组。

        :param filename: 文件名
        :param context: 上下文
        :return: 文件内容的起始偏移量和长度（可能为协程）
        """

        info = self.info(filename)

        context = (self._low.context | context) if context else self._low.context

        if context.read_dont_fetch_header:
            return info.header_offset + info._entry.predicted_lfh_size, info.compress_size

        async def _():
            header: LocalFileHeader = (await self._low.fetch_local_file_header(entry=info._entry))[0]

            if context.read_validate_header_and_entry:
                check_header_and_entry(header, info._entry)

            return info.header_offset + header.total_size, info.compress_size

        return _()

    def range_and_algorithm(self, filename: bytes, context: Context | None = None) -> tuple[int, int, int] | typing.Coroutine[
        typing.Any, typing.Any, tuple[int, int, int]]:
        """
        获取文件内容的区间（start, length）和该文件使用的压缩算法标识
        :param filename: 文件名
        :param context: 上下文
        :return: 一个三元组，包含文件内容区间和压缩算法标识
        """
        info = self.info(filename)

        context = (self._low.context | context) if context else self._low.context

        if context.read_dont_fetch_header:
            return info.header_offset + info._entry.predicted_lfh_size, info.compress_size, info.algorithm

        async def _():
            header: LocalFileHeader = (await self._low.fetch_local_file_header(entry=info._entry))[0]

            if context.read_validate_header_and_entry:
                check_header_and_entry(header, info._entry)

            return info.header_offset + header.total_size, info.compress_size, header.algorithm

        return _()

    def range_and_handler(
            self,
            filename: bytes,
            context: Context | None = None
    ) -> tuple[
        int, int, typing.Callable[[AsyncGenerator[bytes | memoryview | bytearray, None]], AsyncGenerator[
            bytes | memoryview | bytearray, None]]]:
        """
        获取一个文件的内容区间和所需要的处理器
        :param filename: 文件名
        :param context: 上下文信息
        :return: 文件的内容区间和所需要的处理器
        """
        entry = self.info(filename)._entry

        if entry is None:
            raise ValueError(f"Bad entry object for {filename!r}")

        if not is_algorithm_handler_available(entry.algorithm):
            raise UnsupportedAlgorithmError(entry.algorithm)

        ctx = self._low.context if context is None else (self._low.context | context)

        if ctx.read_dont_fetch_header:
            if entry.is_encrypted and entry.algorithm != 0x63:  # ZipCrypto
                from .zip_crypto import zipcrypto_wrapper

                async def handler(raw_data):
                    gen = get_algorithm_handler(entry.algorithm)(
                        zipcrypto_wrapper(raw_data, entry, None, ctx),
                        entry, None, ctx
                    )
                    async for i in gen:
                        yield i

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

        offset = entry.local_header_offset + entry.predicted_lfh_size
        return offset, entry.compress_size, handler

    async def header(self, filename: bytes) -> LocalFileHeader:
        """
        拉取一个文件的本地文件头
        :param filename: 文件名
        :return: 文件的本地文件头
        """
        return (await self._low.fetch_local_file_header(entry=self.info(filename)._entry))[0]

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

    def rglob_info(self, pattern: bytes) -> typing.Generator[FileInfo, None, None]:
        """
        递归模式匹配文件，返回匹配到的文件的 FileInfo 对象
        :param pattern: 模式
        :return: 匹配到的文件的 FileInfo 对象生成器
        """
        for filename, cd in self.infomapping.items():
            if fnmatch(filename, pattern):
                yield cd
