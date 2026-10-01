from typing import AsyncGenerator


class StreamIO:
    __slots__ = ('_g', '_b', '_use')

    def __init__(self, async_gen: AsyncGenerator[bytes | bytearray | memoryview, None]):
        self._g = async_gen
        self._b = bytearray()

        self._use = False

    def _check_in_use(self):
        if self._use:
            raise RuntimeError("StreamIO is already in use.")

    async def read_exactly(self, n: int) -> bytearray:
        if n < 0:
            raise ValueError("Invalid n")

        self._check_in_use()
        b = self._b

        while len(b) < n:
            try:
                b.extend(await anext(self._g))
            except StopAsyncIteration:
                raise EOFError() from None

        d = b[:n]
        del b[:n]

        return d

    async def read(self, n: int) -> bytearray:
        if n < 0 and n != -1:
            raise ValueError("Invalid n")

        self._check_in_use()
        b = self._b

        if n == -1:
            while True:
                try:
                    b.extend(await anext(self._g))
                except StopAsyncIteration:
                    break

            self._b = bytearray()
            return b

        if len(b) >= n:
            d = b[:n]
            del b[:n]
            return d
        else:
            while len(b) < n:
                try:
                    b.extend(await anext(self._g))
                except StopAsyncIteration:
                    self._b = bytearray()
                    return b

            d = b[:n]
            del b[:n]
            return d

    async def raw_stream(self):
        self._check_in_use()
        self._use = True

        try:
            if self._b:
                b = self._b
                self._b = bytearray()
                yield b

            async for chunk in self._g:
                yield chunk
        finally:
            self._use = False
