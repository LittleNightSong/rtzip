from collections.abc import Buffer
from typing import Callable, AsyncGenerator

AlgorithmHandler = Callable[[AsyncGenerator[Buffer, None]], AsyncGenerator[bytes | bytearray | memoryview, None]]

algorithm_mapping: dict[int, AlgorithmHandler] = {}


def algorithm_handler(algorithm: int, override=False):
    if not override and algorithm in algorithm_mapping:
        raise ValueError(f"Algorithm {algorithm} is already registered")

    def decorator(func: AlgorithmHandler):
        algorithm_mapping[algorithm] = func
        return func

    return decorator


def get_algorithm_handler(algorithm: int) -> AlgorithmHandler:
    return algorithm_mapping[algorithm]


def is_algorithm_available(algorithm: int) -> bool:
    return algorithm in algorithm_mapping
