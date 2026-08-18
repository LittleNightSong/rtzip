import dataclasses
import struct
from collections.abc import Buffer
from typing import Annotated, Self

import obstruct as obs

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
        return self.__cstruct__.size

    @property
    def extra_length(self):
        return self.record_size - self.__cstruct__.size

    @classmethod
    def from_buffer(cls, buffer, offset=4, header_only: bool = False):
        self = cls.unpack_from(buffer, offset)

        extra_offset = self.extra_offset + offset
        extra_length = self.extra_length

        if not header_only:
            self.extra_data = buffer[extra_offset: extra_offset + extra_length]
        return self


@dataclasses.dataclass(slots=True)
class CDEntry(obs.Struct):
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
        return bool(self.flags & 0x0001)

    @property
    def is_zip64(self):
        return self.compressed_size == 0xFFFFFFFF or self.original_size == 0xFFFFFFFF

    @property
    def has_data_descriptor(self):
        return bool(self.flags & 0b100)

    @property
    def extra_fields(self) -> dict[int, bytes]:
        if getattr(self, '_extra_fields', None) is None:
            self._extra_fields = parse_extra_fields(self.raw_extra_fields)

        return self._extra_fields

    def fix_by_zip64(self):
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
        buffer = memoryview(buffer)
        # print(buffer.tobytes())
        header_size = cls.__cstruct__.size

        self = cls.unpack_from(buffer, offset)
        filename_len, extra_len, comment_len = self.filename_len, self.extra_len, self.comment_len

        pos = offset + header_size
        filename = bytes(buffer[pos: pos + filename_len])
        pos += filename_len

        extra_attrs = buffer[pos: pos + extra_len].tobytes()
        pos += extra_len

        comment = buffer[pos: pos + comment_len].tobytes()
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
        if self._extra_fields is None:
            self._extra_fields = parse_extra_fields(self.raw_extra_fields)

        return self._extra_fields

    def fix_by_zip64(self):
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
