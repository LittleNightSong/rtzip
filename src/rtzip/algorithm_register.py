from typing import AsyncGenerator, Any, Protocol, runtime_checkable

from .errors import UnsupportedAlgorithmError
from .models import CDEntry, LocalFileHeader

type IndexAcceptedTypes = bytes | bytearray | memoryview


@runtime_checkable
class AlgorithmHandler(Protocol):
    def __call__(
            self,
            raw_data: AsyncGenerator[IndexAcceptedTypes, None],
            entry: CDEntry,
            header: LocalFileHeader,
            context: dict[str, Any],
            /
    ) -> AsyncGenerator[IndexAcceptedTypes, None]:
        """
        压缩算法处理器

        :param raw_data: 原始数据流（异步生成器）
        :param entry: 当前文件的 CDEntry 对象
        :param header: 当前文件的 LocalFileHeader 对象（已经过文件描述符修补）
        :param context: 上下文信息
        :return:
        """
        ...


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
    try:
        return algorithm_mapping[algorithm]
    except KeyError:
        raise UnsupportedAlgorithmError(algorithm)


def get_algorithm_handler_safe(algorithm: int) -> AlgorithmHandler | None:
    return algorithm_mapping.get(algorithm)

def is_algorithm_handlier_available(algorithm: int) -> bool:
    """
    判断某个压缩算法是否已有注册的包装器

    :param algorithm: 压缩算法 ID
    :return:
    """
    return algorithm in algorithm_mapping
