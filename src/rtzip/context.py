import dataclasses


def _merge(ctx: 'Context', other: 'Context') -> 'Context':
    dct = {}

    for f in dataclasses.fields(ctx):
        if f.name == 'extras':
            continue

        x = getattr(ctx, f.name)
        if f.default is not dataclasses.MISSING and f.default is x:
            continue

        if f.default_factory is not dataclasses.MISSING and not bool(x):
            continue

        dct[f.name] = x

    dct['extras'] = dict(ctx.extras)
    dct['extras'].update(other.extras)

    return dataclasses.replace(ctx, **dct)


@dataclasses.dataclass(slots=True, kw_only=True)
class Context:
    """解密加密的 zip 时使用的密码"""
    pwd: bytes | None = None

    # EOCD Scan
    eocd_scan_initial_chunk_size: int = 256
    """扫描 ``EOCD`` 时初始的拉取分块大小"""
    eocd_scan_max_retry: int = 4
    """扫描 ``EOCD`` 时最大拉取新分块次数"""
    eocd_scan_rechunk_factor: int | float = 1
    """当一次拉取失败时候重设分块大小乘以的系数"""
    eocd_scan_use_cache: bool = True
    """如果实例上已有拉取到的 ``EOCD`` 是否直接使用缓存而跳过扫描"""

    # Read Action
    read_dont_fetch_header: bool = False
    """读取的时候不拉取本地文件头，直接从中央目录入口对象中推断文件起始偏移量"""
    read_validate_header_and_entry: bool = False
    """读取时对本地文件头和中央目录入口对象进行交叉验证"""
    read_size_max: int | None = None
    """读取时最大允许的字节数（解压后），防止 Zip Bomp，默认关闭"""
    read_size_max_overflow: int | None = None
    """读取时最大允许的超出元数据声明的字节数，默认关闭，可以被 ``read_size_max`` 覆盖"""
    read_size_max_overflow_percent: int | float | None = None
    """读取时最大允许超出元数据声明的字节数占比，防止 Zip Bomp，默认关闭，
    可以被 ``read_size_max`` 或 ``read_size_max_overflow`` 覆盖"""

    # Extras
    extras: dict = dataclasses.field(default_factory=dict)
    """额外的配置项"""

    # Helpers

    def __setattr__(self, name, value):
        try:
            super(Context, self).__setattr__(name, value)
        except AttributeError:
            self.extras[name] = value

    def __getattr__(self, name):
        if name in self.extras:
            return self.extras[name]
        else:
            raise AttributeError(name)

    def __or__(self, other):
        if isinstance(other, Context):
            return _merge(self, other)

        elif isinstance(other, dict):
            other_extras = {
                k: v
                for k, v in other.items()
                if k not in self.extras
            }

            extras = dict(self.extras)
            extras.update(other_extras)

            other_normals = {
                k: v
                for k, v in other.items()
                if k in self.extras
            }

            return dataclasses.replace(
                self, **other_normals, extras=extras
            )

        else:
            return NotImplemented


_context_fields = set(dataclasses.fields(Context))
