import dataclasses
import struct
from collections.abc import Buffer
from typing import Annotated, Self

import obstruct as obs

from rtzip.utils import exactly_get_slice

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
class EOCD(obs.Struct):
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
    disk_num: obs.u16
    cd_disk: obs.u16
    disk_entries: obs.u16
    total_entries: obs.u16
    cd_size: obs.u32
    cd_offset: obs.u32
    comment_length: obs.u16

    @classmethod
    def from_buffer(cls, buffer: Buffer, offset=4):
        return cls.unpack_from(buffer, offset)


@dataclasses.dataclass(slots=True)
class Zip64EOCD(obs.Struct):
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
    # 签名部分已忽略
    record_size: obs.u64
    version: obs.u16
    unzip_min_version: obs.u16
    disk_id: obs.u32
    cd_disk: obs.u32
    disk_entries: obs.u64
    total_entries: obs.u64
    cd_size: obs.u64
    cd_offset: obs.u64
    extra_data: Annotated[bytes, obs.Marks.NOT_A_CFIELD]

    @property
    def extra_offset(self):
        """
        扩展信息的起始偏移量（相对于此结构体的起始部分）
        :return:
        """
        return self.__cstruct__.size

    @property
    def extra_length(self):
        """
        扩展字段的长度，通过 record_size 反推
        :return:
        """
        return self.record_size - self.__cstruct__.size

    @classmethod
    def from_buffer(cls, buffer, offset=4, header_only: bool = False):
        """
        从缓冲区读取 Zip64EOCD 信息

        :param buffer: 缓冲区对象
        :param offset: 读取时的起始偏移量
        :param header_only: 是否只解析固定的头部信息
        :return:
        """
        self = cls.unpack_from(buffer, offset)

        extra_offset = self.extra_offset + offset
        extra_length = self.extra_length

        if not header_only:
            self.extra_data = buffer[extra_offset: extra_offset + extra_length]
        return self


@dataclasses.dataclass(slots=True)
class CDEntry(obs.Struct):
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
    :ivar inner_file_attrs: 内部文件属性信息
    :ivar outer_file_attrs: 外部文件属性信息
    :ivar local_header_offset: 本地文件头的起始偏移量

    :ivar filename: 文件名（未解码）
    :ivar raw_extra_fields: 原始扩展信息字节串
    :ivar comment: 文件注释（未解码）
    """
    version: obs.u16
    unzip_min_version: obs.u16
    flags: obs.u16
    algorithm: obs.u16
    last_modified: obs.u16
    last_modified_date: obs.u16
    crc32_raw: obs.u32
    compressed_size: obs.u32
    original_size: obs.u32
    filename_len: obs.u16
    extra_len: obs.u16
    comment_len: obs.u16
    disk_id: obs.u16
    inner_file_attrs: obs.u16
    outer_file_attrs: obs.u32
    local_header_offset: obs.u32

    filename: Annotated[bytes, obs.Marks.NOT_A_CFIELD]
    raw_extra_fields: Annotated[bytes, obs.Marks.NOT_A_CFIELD]
    comment: Annotated[bytes, obs.Marks.NOT_A_CFIELD]

    _extra_fields: Annotated[dict | None, obs.Marks.NOT_A_CFIELD] = dataclasses.field(default=None, repr=False)

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
    def extra_fields(self) -> dict[int, bytes]:
        """
        解析后的扩展字段字典
        映射关系为 header-id -> raw-data
        :return: dict[int, bytes]
        """
        if getattr(self, '_extra_fields', None) is None:
            self._extra_fields = parse_extra_fields(self.raw_extra_fields)

        return self._extra_fields

    def fix_by_zip64(self):
        """
        自动尝试获取 zip64 扩展信息，并补充当前文件对象的缺失信息
        :return:
        """
        zip64_data = (self.extra_fields.get(1))
        offset = 0
        if zip64_data:
            zip64_data = memoryview(zip64_data)
            length = len(zip64_data)
            if self.original_size == 0xFFFFFFFF:
                assert offset + 8 <= length
                self.original_size = int.from_bytes(zip64_data[offset:offset + 8], 'little')
                offset += 8
            if self.compressed_size == 0xFFFFFFFF:
                assert offset + 8 <= length
                self.compressed_size = int.from_bytes(zip64_data[offset:offset + 8], 'little')
                offset += 8
            if self.local_header_offset == 0xFFFFFFFF:
                assert offset + 8 <= length
                self.local_header_offset = int.from_bytes(zip64_data[offset: offset + 8], 'little')
                offset += 8
            if self.disk_id == 0xFFFF:
                assert offset + 4 <= length
                self.disk_id = int.from_bytes(zip64_data[offset: offset + 4], 'little')

    @classmethod
    def from_buffer(cls, buffer: Buffer, offset=4) -> tuple[Self, int]:
        """
        从缓冲区读取 CDEntry 信息，默认 offset=4 以跳过签名信息）

        :param buffer: 缓冲区对象
        :param offset: 读取起始偏移量
        :return: 一个 CDEntry 实例，以及解析时消耗的字节数（不包含 offset）
        """
        buffer = memoryview(buffer)
        # print(buffer.tobytes())
        header_size = cls.__cstruct__.size

        self = cls.unpack_from(buffer, offset)
        filename_len, extra_len, comment_len = self.filename_len, self.extra_len, self.comment_len

        pos = offset + header_size
        filename = bytes(exactly_get_slice(buffer, pos, filename_len))
        pos += filename_len

        extra_attrs = bytes(exactly_get_slice(buffer, pos, extra_len))
        pos += extra_len

        comment = bytes(exactly_get_slice(buffer, pos, comment_len))
        pos += comment_len

        self.filename = filename
        self.raw_extra_fields = extra_attrs
        self.comment = comment

        # print(self)

        return (
            self,
            pos - offset
        )


@dataclasses.dataclass()
class LocalFileHeader(obs.Struct):
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
    unzip_min_version: obs.u16
    flags: obs.u16
    algorithm: obs.u16
    last_modified_time: obs.u16
    last_modified_date: obs.u16
    crc32: obs.u32
    compressed_size: obs.u32
    uncompressed_size: obs.u32
    filename_len: obs.u16
    extra_len: obs.u16

    filename: Annotated[bytes, obs.Marks.NOT_A_CFIELD]
    raw_extra_fields: Annotated[bytes, obs.Marks.NOT_A_CFIELD]

    _extra_fields: Annotated[dict[int, bytes] | None, obs.Marks.NOT_A_CFIELD] = None

    @property
    def extra_fields(self):
        """
        解析后的扩展信息字典
        映射关系为 header-id -> raw-data
        :return: 一个字典，包含扩展头和数据的映射
        """
        if self._extra_fields is None:
            self._extra_fields = parse_extra_fields(self.raw_extra_fields)

        return self._extra_fields

    def fix_by_zip64(self):
        """
        自动从 zip64 扩展提取信息并补充缺失信息
        :return: None
        """
        zip64_data = self.extra_fields.get(0x0001)
        if zip64_data:
            original_size, compressed_size = struct.unpack('<QQ', zip64_data)
            self.uncompressed_size = original_size
            self.compressed_size = compressed_size

    @classmethod
    def from_buffer(cls, buffer: Buffer, offset: int = 4, header_only=False) -> tuple[Self, int]:
        """从缓冲区解析 Local File Header，返回 (实例, 消耗字节数)"""
        buf = memoryview(buffer)
        header_size = cls.__cstruct__.size

        # 解析固定头部
        self = cls.unpack_from(buf, offset)

        if header_only:
            return self, header_size

        # 提取长度字段
        filename_len = self.filename_len
        extra_len = self.extra_len

        pos = offset + header_size

        # 读取文件名
        filename = bytes(buf[pos:pos + filename_len])
        pos += filename_len

        # 读取额外字段
        extra_fields = bytes(buf[pos:pos + extra_len])
        pos += extra_len

        self.filename = filename
        self.raw_extra_fields = extra_fields

        return (
            self,
            pos - offset
        )

    @property
    def data_offset(self) -> int:
        """返回实际文件数据的起始偏移量（相对于 Local File Header 起始位置）"""
        # 固定头 30 字节 + 文件名长度 + 额外字段长度
        return self.__cstruct__.size + self.filename_len + self.extra_len + 4
