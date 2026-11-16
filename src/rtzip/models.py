import dataclasses
import struct
from collections.abc import Buffer
from typing import Annotated, Self


EXTRA_FIELD_HEADER = struct.Struct("<HH")


def parse_extra_fields(buffer):
    res = {}

    buffer = memoryview(buffer)
    length = buffer.nbytes
    offset = 0

    while offset + 4 <= length:
        header_id, n_data = EXTRA_FIELD_HEADER.unpack_from(buffer, offset)

        offset += 4
        res[header_id] = buffer[offset: offset + n_data].tobytes()
        offset += n_data

    return res


@dataclasses.dataclass(slots=True)
class EOCD:
    """
    zip 信息（EOCD）

    :ivar disk_num: 分卷数量
    :ivar cd_disk: 中央目录位于的分卷编号
    :ivar disk_entries: 当前分卷包含的文件数量
    :ivar total_entries: 该压缩包包含的所有文件数量
    :ivar cd_size: 中央目录大小（字节数）
    :ivar cd_offset: 中央目录的起始偏移量（在本文件中）
    :ivar comment_length: 注释的长度，注释紧跟在 EOCD 的后方
    """
    __STRUCT__ = struct.Struct("<HHHHIIH")

    disk_num: int
    cd_disk: int
    disk_entries: int
    total_entries: int
    cd_size: int
    cd_offset: int
    comment_length: int

    @classmethod
    def from_buffer(cls, buffer: Buffer, offset=4):
        return cls(
            *cls.__STRUCT__.unpack_from(buffer, offset)
        )


@dataclasses.dataclass(slots=True)
class Zip64EOCD:
    """
    启用 Zip64 扩展后的 EOCD 结构
    当启动 Zip64 扩展后，原先的 EOCD 的关键字段将会置 -1，此时需要读取 Zip64 的 EOCD 结构

    :ivar record_size: Zip64EOCD 结构的总长（不包含这个字段本身）
    :ivar version: 打包者使用的 zip 版本
    :ivar unzip_min_version: 解压时最低的版本要求
    :ivar disk_id: 当前压缩包文件对应的分卷
    :ivar cd_disk: 中央目录位于的分卷号
    :ivar disk_entries: 当前分卷上所有的文件数量
    :ivar total_entries: 压缩包（包括所有分卷）包含的所有文件数量
    :ivar cd_size: 中央目录的大小（字节数）
    :ivar cd_offset: 中央仓库在此文件中的起始偏移量
    :ivar extra_data: Zip64 标准添加的扩展数据段，可变长，一般为空
    """
    __STRUCT__ = struct.Struct("<QHHIIQQQQ")

    # 签名部分已忽略
    record_size: int
    version: int
    unzip_min_version: int
    disk_id: int
    cd_disk: int
    disk_entries: int
    total_entries: int
    cd_size: int
    cd_offset: int
    extra_data: bytes

    header_size = 52

    @property
    def extra_length(self):
        """
        扩展字段的长度，通过 record_size 反推
        :return:
        """
        return self.record_size - 52

    @classmethod
    def from_buffer(cls, buffer, offset=4):
        """
        从缓冲区读取 Zip64EOCD 信息

        :param buffer: 缓冲区对象
        :param offset: 读取时的起始偏移量
        :param
        :return:
        """
        values = cls.__STRUCT__.unpack_from(buffer, offset)
        self = cls(*values, extra_data=b'')
        return self

    def fix_missing(self, data_after: Buffer, offset: int):
        buf = memoryview(data_after)

        if len(buf) - offset < self.extra_length:
            from .errors import NotEnoughDataError
            raise NotEnoughDataError(self.extra_data, len(buf) - offset)

        self.extra_data = bytes(buf[offset: offset + self.extra_length])


@dataclasses.dataclass(slots=True)
class CDEntry:
    """
    中央目录文件信息项

    :ivar version: 打包者使用的 Zip 版本
    :ivar unzip_min_version: 解压时需要使用的最低 zip 版本
    :ivar flags: 通用标志位
    :ivar algorithm: 压缩算法编号
    :ivar last_modified: 文件的上次修改时间
    :ivar last_modified_date: 文件的上次修改日期
    :ivar crc32_raw: CRC32 校验信息（这里使用整数存放）
    :ivar compressed_size: 文件的压缩后大小
    :ivar original_size: 文件的未压缩大小
    :ivar filename_len: 文件名的长度
    :ivar extra_len: 扩展信息的长度
    :ivar disk_id: 文件位于的分卷编号
    :ivar internal_file_attrs: 内部文件属性信息
    :ivar external_file_attrs: 外部文件属性信息
    :ivar local_header_offset: 本地文件头的起始偏移量

    :ivar filename: 文件名（未解码）
    :ivar raw_extra_fields: 原始扩展信息字节串
    :ivar comment: 文件注释（未解码）
    """

    """
    创建者使用的 zip 格式版本
    """

    __STRUCT__ = struct.Struct("<HHHHHHIIIHHHHHII")

    version: int

    unzip_min_version: int
    flags: int
    algorithm: int
    last_modified: int
    last_modified_date: int
    crc32_raw: int
    compressed_size: int
    original_size: int
    filename_len: int
    extra_len: int
    comment_len: int
    disk_id: int
    internal_file_attrs: int
    external_file_attrs: int
    local_header_offset: int

    filename: bytes
    raw_extra_fields: bytes
    comment: bytes

    _extra_fields: dict[int, bytes] | None = dataclasses.field(default=None, repr=False)

    @property
    def is_encrypted(self):
        """
        该文件是否被密码保护
        :return: 一个 bool 值，表示文件是否被密码保护
        """
        return bool(self.flags & 0x0001)

    @property
    def is_zip64(self):
        """
        是否需要 zip64 扩展才能获取信息
        :return:
        """
        return self.compressed_size == 0xFFFFFFFF or self.original_size == 0xFFFFFFFF

    @property
    def has_data_descriptor(self):
        """
        是否使用了数据描述符协议
        若使用，则不应该信任本地文件头中的长度信息

        :return: 一个 bool 值，表示该文件条目是否使用了数据描述符协议
        """
        return bool(self.flags & 0b100)

    @property
    def is_symlink(self):
        return (self.external_file_attrs >> 16) & 0xF000 == 0xA000

    @property
    def extra_fields(self) -> dict[int, bytes]:
        """
        解析后的扩展字段字典
        映射关系为 header-id -> raw-data
        :return: dict[int, bytes]
        """
        if getattr(self, '_extra_fields', None) is None:
            self._extra_fields = parse_extra_fields(self.raw_extra_fields)

        # pyrefly: ignore[bad-return]
        return self._extra_fields

    @property
    def total_size(self):
        """
        此入口点对象所占用的字节数，包括 4 字节的签名和变长字段
        :return:
        """
        # Signature (4) + Header(42) + Filename + Extra Fields
        return 46 + self.filename_len + self.extra_len

    @property
    def predicted_lfh_length(self):
        return 30 + self.filename_len + self.extra_len  # Signature(4) + LocalFileHeader(26) + Filename + Extra Fields

    def fix_by_zip64(self):
        """
        自动尝试获取 zip64 扩展信息，并补充当前文件对象的缺失信息
        :return:
        """
        zip64_data = (self.extra_fields.get(1))
        if zip64_data:
            z = memoryview(zip64_data)
            length = len(z)
            offset = 0

            def readint(n=8, /):
                nonlocal offset
                offset += n
                if offset > length:
                    from .errors import NotEnoughDataError
                    raise NotEnoughDataError(n, length - offset + n)

                return int.from_bytes(z[offset-n:offset], 'little')

            if self.original_size == 0xFFFFFFFF:
                self.original_size = readint()
            if self.compressed_size == 0xFFFFFFFF:
                self.compressed_size = readint()
            if self.local_header_offset == 0xFFFFFFFF:
                self.local_header_offset = readint()
            if self.disk_id == 0xFFFF:
                self.disk_id = readint(4)

    @classmethod
    def from_buffer(cls, buffer: Buffer, offset=4) -> Self:
        """
        从缓冲区读取 CDEntry 内容信息（默认 offset=4 以跳过签名信息）

        :param buffer: 缓冲区对象
        :param offset: 读取起始偏移量
        :return: 一个 CDEntry 实例
        """
        buffer = memoryview(buffer)
        values = cls.__STRUCT__.unpack_from(buffer, offset)
        self = cls(*values, filename=b'', comment=b'', raw_extra_fields=b'')
        self.fix_missing(buffer, offset + 42)

        return self

    def fix_missing(self, data_after: Buffer, offset: int):
        buf = memoryview(data_after)
        filename_len, extra_len, comment_len = self.filename_len, self.extra_len, self.comment_len

        if len(buf) - offset < filename_len + extra_len + comment_len:
            from .errors import NotEnoughDataError
            raise NotEnoughDataError(filename_len + extra_len + comment_len, len(buf) - offset)

        pos = offset
        self.filename = bytes(buf[pos:pos+filename_len])
        pos += filename_len

        self.raw_extra_fields = bytes(buf[pos:pos+extra_len])
        pos += extra_len

        self.comment = bytes(buf[pos:pos+comment_len])
        pos += comment_len


@dataclasses.dataclass(slots=True)
class LocalFileHeader:
    """
    本地文件头，位于文件内容之前，包含这个文件的元数据

    :ivar unzip_min_version: 解压此文件所需的最低 zip 版本
    :ivar flags: 通用标志位
    :ivar algorithm: 压缩算法编号
    :ivar last_modified_time: 文件的上次修改时间
    :ivar last_modified_date: 文件的上次修改日期
    :ivar crc32: CRC32 校验信息（这里使用整数存放）
    :ivar compressed_size: 文件的压缩后大小
    :ivar uncompressed_size: 文件的未压缩大小
    :ivar filename_len: 文件名的长度
    :ivar extra_len: 扩展信息的长度

    :ivar filename: 文件名
    :ivar raw_extra_fields: 原始扩展数据字节串
    """
    __STRUCT__ = struct.Struct("<HHHHHIIIHH")

    unzip_min_version: int
    flags: int
    algorithm: int
    last_modified_time: int
    last_modified_date: int
    crc32: int
    compressed_size: int
    uncompressed_size: int
    filename_len: int
    extra_len: int

    filename: bytes
    raw_extra_fields: bytes

    _extra_fields: dict[int, bytes] | None = None

    @property
    def extra_fields(self) -> dict[int, bytes]:
        """
        解析后的扩展信息字典
        映射关系为 header-id -> raw-data
        :return: 一个字典，包含扩展头和数据的映射
        """
        if self._extra_fields is None:
            self._extra_fields = parse_extra_fields(self.raw_extra_fields)

        return self._extra_fields

    @property
    def total_size(self) -> int:
        """返回头部数据总大小（包含签名大小）"""
        # Signature(4) + Header(26) + Filename(n) + Extra Fields(m)
        return 30 + self.filename_len + self.extra_len

    def fix_zip64(self):
        """
        自动从 zip64 扩展提取信息并补充缺失信息
        :return: None
        """
        if zip64_data := self.extra_fields.get(0x0001):
            original_size, compressed_size = struct.unpack('<QQ', zip64_data)
            self.uncompressed_size = original_size
            self.compressed_size = compressed_size

    @classmethod
    def from_buffer(cls, buffer: Buffer, offset: int = 4) -> Self:
        """从缓冲区解析 Local File Header，返回 (实例, 消耗字节数)"""
        # 解析固定头部
        values = cls.__STRUCT__.unpack_from(buffer, offset)
        self = cls(*values, filename=b'', raw_extra_fields=b'')
        self.fix_missing(buffer, offset + 26)

        return self

    def fix_missing(self, data_after: Buffer, offset: int):
        buf = memoryview(data_after)
        pos = offset

        if len(buf) - offset < self.total_size:
            raise

        _ = self.filename_len
        self.filename = bytes(buf[pos:pos + _])
        pos += _

        _ = self.extra_len
        self.raw_extra_fields = bytes(buf[pos:pos + _])
        pos += _

        return self


# if __name__ == '__main__':
#     def show_struct(typ):
#         print(f'{typ.__name__}: ({typ.__cendian__} "{typ.__ctype__}")')
#         for field in dataclasses.fields(typ):
#             if field.default is not dataclasses.MISSING:
#                 print(f'\t{field.name}: {field.type.__name__} = {field.default}')
#             elif field.default_factory is not dataclasses.MISSING:
#                 print(f'\t{field.name}: {field.type.__name__} = {field.default_factory}()')
#             else:
#                 print(f'\t{field.name}: {field.type.__name__}')
#
#     show_struct(EOCD)
#     show_struct(Zip64EOCD)
#     show_struct(CDEntry)
#     show_struct(LocalFileHeader)

