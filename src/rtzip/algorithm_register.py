from collections.abc import Buffer
from typing import Callable, AsyncGenerator, Any

from .models import CDEntry, LocalFileHeader

AlgorithmHandler = Callable[[AsyncGenerator[Buffer, None], CDEntry, LocalFileHeader, dict[str, Any]], AsyncGenerator[bytes | bytearray | memoryview, None]]

algorithm_mapping: dict[int, AlgorithmHandler] = {}


def algorithm_handler(algorithm: int, override=False):
    """
    压缩流包装器注册装饰器

    :param algorithm: 压缩算法 ID
    :param override: 如果你希望覆盖一个已注册的包装器，请设置为 True，否则将会阻止可能的覆盖行为
    :return:
    """
    if not override and algorithm in algorithm_mapping:
        raise ValueError(f"Algorithm {algorithm} is already registered")

    def decorator(func: AlgorithmHandler):
        algorithm_mapping[algorithm] = func
        return func

    return decorator


def get_algorithm_handler(algorithm: int) -> AlgorithmHandler:
    """
    获取包装器函数

    :param algorithm: 压缩算法 ID
    :return: AlgorithmHandler 可调用对象，接受一个异步生成器，返回一个异步生成器
    """
    return algorithm_mapping[algorithm]


def is_algorithm_available(algorithm: int) -> bool:
    """
    判断某个压缩算法是否已有注册的包装器

    :param algorithm: 压缩算法 ID
    :return:
    """
    return algorithm in algorithm_mapping
