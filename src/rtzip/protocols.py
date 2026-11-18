import typing
from typing import AsyncGenerator


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
