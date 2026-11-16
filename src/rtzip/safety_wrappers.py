from typing import AsyncGenerator


async def size_guard(gen: AsyncGenerator, max_size: int | float = float("inf")):
    t = 0

    async for i in gen:
        t += len(i)
        if t > max_size:
            raise ValueError()  # TODO: Add error message

        yield t


