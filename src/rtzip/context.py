import dataclasses


def _merge(ctx: 'Context', other: 'Context') -> 'Context':
    dct = {}

    for f in dataclasses.fields(Context):
        if f.name == 'extras':
            continue

        if f.name in other._modifiers:
            dct[f.name] = getattr(other, f.name)

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

    _initialized: bool = dataclasses.field(default=False, init=False, repr=False)
    _modifiers: set = dataclasses.field(default_factory=set, init=False, repr=False)

    # _extra_modifiers: set = dataclasses.field(default_factory=set, init=False, repr=False)

    # Helpers
    def __post_init__(self):
        self._initialized = True

    def __setattr__(self, name, value):
        if name == '_initialized':
            super(Context, self).__setattr__(name, value)
            return
        try:
            super(Context, self).__setattr__(name, value)
            if self._initialized:
                self._modifiers.add(name)
        except AttributeError:
            # self._extra_modifiers.add(name)
            self.extras[name] = value

    def __getattr__(self, name):
        if name in self.extras:
            return self.extras[name]
        else:
            raise AttributeError(name)

    def __or__(self, other):
        if isinstance(other, Context):
            return _merge(self, other)

        else:
            return NotImplemented


_old_init = Context.__init__


def _init(self, *args, **kwargs):
    self._initialized = False
    _old_init(self, *args, **kwargs)

    for field in dataclasses.fields(Context)[:len(args)]:
        self._modifiers.add(field.name)

    for k in kwargs.keys():
        self._modifiers.add(k)


Context.__init__ = _init
