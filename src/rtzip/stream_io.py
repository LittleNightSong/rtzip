from contextlib import contextmanager
from typing import AsyncGenerator


class StreamIO:
    __slots__ = ('_data_generator', '_buffer', '_pos', '_in_use', '_at_eof')

    def __init__(
            self,
            async_gen: AsyncGenerator[bytes | bytearray | memoryview, None]
    ):
        self._data_generator = async_gen
        self._pos = 0
        self._buffer = bytearray()
        self._at_eof = False

        self._in_use = False

    def _check_in_use(self):
        if self._in_use:
            raise RuntimeError("StreamIO is already in use.")

    @contextmanager
    def _use(self):
        self._check_in_use()
        self._in_use = True
        try:
            yield
        finally:
            self._in_use = False

    def _advance_pos(self, pos: int):
        self._pos += pos
        if self._pos >= 1024:
            del self._buffer[:self._pos]
            self._pos = 0

        elif self._pos == len(self._buffer) != 0:
            self._buffer = bytearray()
            self._pos = 0

    @property
    def at_eof(self):
        return self._at_eof

    @property
    def in_use(self):
        return self._in_use

    async def read_exactly(self, n: int) -> bytearray:
        if n < 0:
            raise ValueError("Invalid n")

        with self._use():
            buf = self._buffer

            while len(buf) < n:
                try:
                    buf.extend(await anext(self._data_generator))
                except StopAsyncIteration:
                    self._at_eof = True
                    raise EOFError() from None

            data = buf[self._pos:self._pos + n]

            self._advance_pos(n)

            return data

    async def read(self, n: int) -> bytearray:
        if n < 0 and n != -1:
            raise ValueError("Invalid n")

        with self._use():
            buf = self._buffer

            if n == -1:
                while True:
                    try:
                        buf.extend(await anext(self._data_generator))
                    except StopAsyncIteration:
                        self._at_eof = True
                        break

                self._buffer = bytearray()
                self._pos = 0

                return buf

            while len(buf) < n:
                try:
                    buf.extend(await anext(self._data_generator))
                except StopAsyncIteration:
                    # 读不满 n 字节，也直接返回
                    self._at_eof = True
                    self._buffer = bytearray()
                    self._pos = 0
                    return buf

            data = buf[self._pos:self._pos + n]
            self._advance_pos(n)
            # 一定要在读取数据之后再 advance,不然一旦触发 compress 就会使数据被删除
            return data

    async def stream_elapsed(self):
        with self._use():
            if len(self._buffer) != self._pos:
                data = memoryview(self._buffer)[self._pos:]
                self._buffer = bytearray()
                self._pos = 0
                yield data

            async for chunk in self._data_generator:
                yield chunk

            self._at_eof = True
