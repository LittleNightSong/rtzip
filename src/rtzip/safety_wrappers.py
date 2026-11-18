from typing import AsyncGenerator


async def guard_max_size(gen: AsyncGenerator, max_size: int | float = float("inf")):
    t = 0

    async for i in gen:
        t += len(i)
        if t > max_size:
            raise OverflowError("Unzip stream is too large.")

        yield t


def guard_overflow_size(gen: AsyncGenerator, expected_size: int, max_overflow: int = 0):
    return guard_max_size(gen, max_overflow + expected_size)


def guard_overflow_size_percent(gen: AsyncGenerator, expected_size: int, percent: float):
    return guard_max_size(gen, expected_size * (percent + 1))
