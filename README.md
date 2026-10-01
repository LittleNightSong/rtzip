# Rtzip

_一个以最小的数据量提取远程 zip 文件内容的库_

## 特点

- 不与任何 HTTP 库耦合
- 依赖简单（只有用于解析二进制结构的 `obstruct`）
- 兼容标准 zip 格式、ZIP64 扩展

## 安装

```commandline
pip install rtzip
```

## 用法

### 准备数据源

你需要先定义一个自己的数据源，再将其传递给 `RemoteZip`。

数据源需要包含三个方法：

- `read_range(offset, length) -> bytes | bytearray | memoryview`: 随机读取某个数据段
- `read_range_stream(offset, length) -> AsyncGenerator[bytes | bytearray | memoryview, None]`: 流式随机读取某个数据段
- `get_total_size() -> int`: 获取源的总大小（用于 Zip-EOCD 查找）

```python
from rtzip import RemoteZip, DataSource

import niquests  # 如果你使用 niquests 作为 http 依赖的话


class HTTPSource(DataSource):
    def __init__(self, url, session=None):
        self.url = url
        self.session = session or niquests.AsyncSession()

    async def _request_range(self, offset, length):
        resp = await self.session.get(
            self.url,
            headers={'Range': f"bytes={offset}-{offset + length - 1}"},
            stream=True,
        )
        resp.raise_for_status()
        if resp.status_code != 206:
            await resp.close()
            raise niquests.HTTPError(response=resp)
        return resp

    async def read_range(self, offset, length):
        return await (await self._request_range(offset, length)).content

    async def read_range_stream(self, offset, length):
        async for i in await (await self._request_range(offset, length)).iter_content():
            yield i

    async def get_total_size(self):
        resp = await self.session.head(self.url)
        resp.raise_for_status()
        return int(resp['content-length'])

```

### 创建实例

```python
rz = RemoteZip(HTTPSource(url=..., session=...), pwd=b'<password-or-None>')
```

> [!NOTE]
> `pwd` 为仅关键字参数，用于为加密 zip 文件提供解密密码；不使用加密时可以不传。

`rtzip` 在实现上分为两层：

- **Low-level**：`LowLevelRemoteZip`，提供 `lowerlevel_*` 前缀的底层方法，全部为异步，直接操作原始路径，不自动做路径规范化，也不自动维护规范化映射表。
- **High-level**：`RemoteZip`（继承自 `LowLevelRemoteZip`），提供以规范化路径为核心的便捷接口，同时保留了全部 Low-level 方法。

---

### Low-level API

Low-level API 适合需要精细控制元数据拉取时机、或者希望复用底层缓存的场景。
所有 `lowerlevel_*` 方法默认都会缓存结果（`reuse=True`），可通过传入 `reuse=False` 强制重新拉取并覆盖缓存。

#### 拉取 EOCD

```python
# 显式拉取 EOCD 信息
# 返回一个 `rtzip.models.EOCD` 或 `rtzip.models.Zip64EOCD` 对象
eocd = await rz.lowerlevel_fetch_eocd()
```

支持的参数：

- `reuse=True`：命中缓存则直接返回
- `initial_chunk=4096`：初始扫描的字节数
- `max_cnt=-1`：最大重试次数（`-1` 表示不限）
- `loss_factor=2`：每次重试后扫描区间的放大系数

#### 拉取中央目录

```python
# 拉取中央目录的信息（文件列表）
files = await rz.lowerlevel_fetch_central_directory()
# 返回一个包含 `rtzip.models.CDEntry` 对象的列表
# 如果还未拉取 EOCD，则将自动拉取 EOCD
```

支持的参数：

- `ignore_extra=False`：是否忽略尾部无法解析的多余数据
- `reuse=True`：命中缓存则直接返回

#### 拉取本地文件头

```python
# 获取本地文件头信息
# 返回一个二元组 `(header, buffer)`
# - header 是 `rtzip.models.LocalFileHeader` 对象
# - buffer 是解析头部时顺带读取到的额外数据（当文件足够小时可直接复用）
header, extra = await rz.lowerlevel_fetch_local_file_header(filename=...)

# 你也可以通过 `rtzip.models.CDEntry` 对象拉取本地文件头，这样可以跳过文件名查找
header, extra = await rz.lowerlevel_fetch_local_file_header(entry=...)
```

#### 构建映射与查找

```python
# 构建「原始文件名 -> CDEntry」映射表（覆盖已有映射）
mapping = rz.lowlevel_build_mapping()

# 通过原始路径精确查找 CDEntry
# 如果已存在 lowlevel_file_mapping，则直接命中；否则线性遍历中央目录
entry = rz.lowerlevel_find_file_entry(b'<filename>')
```

> [!NOTE]
> `lowerlevel_find_file_entry` 要求中央目录已被拉取，否则抛出 `ValueError`；
> 找不到文件时抛出 `FileNotFoundError`。

---

### High-level API

High-level API 以规范化路径（[normpath](src/rtzip/path_methods.py)）为统一入口，适合绝大多数使用场景。
建议先调用一次 `init()` 完成元数据初始化，之后大部分查询方法都可以同步调用。

#### 初始化与一次性元数据加载

```python
# 一次性拉取 EOCD、中央目录并建立文件名 -> CDEntry 映射
await rz.init()

# 获取所有的文件名（bytes）列表
names: list[bytes] = rz.namelist()

# 获取「规范化文件名 -> CDEntry」的映射
mapping = rz.namemapping()

# 手动重建映射表（在中央目录变更后调用）
rz.build_mapping()
```

> [!TIP]
> 大多数 `RemoteZip` 的元数据操作（`entry`、`exists`、`listdir` 等）都依赖 `namemapping()`，
> 在 `init()`（或 `build_mapping()`）之后它们可以直接同步调用。

#### 元数据查询

```python
# 通过规范化路径查找 CDEntry
entry = rz.entry(b'<filename>')

# 文件存在性
if rz.exists(b'<filename>'):
    ...
else:
    ...

# 获取指定目录下的子项（基于文件名前缀且不包含目录本身）
files: list[bytes] = rz.listdir(b'<filename>')

# 模式匹配
files = rz.rglob(b"*.py")

entries = rz.rglob_entries(b"*.py")

# 大小获取
original_size = rz.original_size(b'<filename>')
compressed_size = rz.compressed_size(b'<filename>')

# 拉取本地文件头（返回 (header, buffer) 二元组）
header, extra = await rz.header(b'<filename>')
```

> [!NOTE]
> 与 `lowerlevel_find_file_entry`（使用原始路径、精确匹配）不同，
> `entry` 会对传入路径做规范化处理，更贴近直觉，但不会复用 `lowlevel_file_mapping`，
> 因而在某些场景下可能有轻微的性能损失。

#### 文件读取

```python
data: bytearray = await rz.read(b'<filename>')

async for chunk in rz.stream(b'<filename>'):
    print(chunk)

# 直接以文本形式读取（底层仍然走 read）
text = await rz.read_text(b'<filename>', encoding='utf-8', errors='strict')
```

> [!TIP]
> 更具体的文档参见方法和类的文档注释

---

### 自定义压缩算法支持

```python
# 示例：扩展 PPMd 算法支持（需要 pip install ppmd）
import ppmd
from rtzip import algorithm_handler


@algorithm_handler(0x000A)
async def ppmd_wrapper(raw_generator, entry, header, ctx):
    # PPMd 流式解压（API 取决于具体库）
    decomp = ppmd.Ppmd7Decompressor()
    async for chunk in raw_generator:
        yield decomp.decompress(chunk)


# 这之后使用 RemoteZip 时遇到 ppmd 算法会自动应用你的包装器

# 如果想要重写某个内置的包装器
# 你需要指定 `override=True`，否则程序将会阻止覆盖行为
@algorithm_handler(0x0008, override=True)
async def your_deflate_wrapper(raw_generator, entry, header, ctx):
    ...

# raw generator 是一个异步生成器，每次迭代产生一个 bytes/bytearray/memoryview 对象
# entry 是这个文件的 CDEntry 对象
# header 是这个文件的 LocalFileHeader 对象
# ctx 是一个字典，一般为调用 read 或其它方法的 kwargs

```

### 启用加密文件支持

启用很简单，rtzip 提供了两种不同的加密后端实现：`pycryptodome` 以及 `cryptography`。
但是我们更建议使用 `pycryptodome`，因为 cryptography 的某些局限性，项目不得不在性能上做出妥协。

#### pycryptodome

```python
from rtzip.wzaes_backends import pycryptodome_impl

pycryptodome_impl.install()
```

#### cryptography (已弃用)

如果没有 `cryptography` 的强制需求，我们更建议使用 `pycryptodome`。

```python
from rtzip.wzaes_backends import cryptography_impl

cryptography_impl.install()

```

#### 自定义解密后端

自定义后端要求你提供两个方法的实现：`PBKDF2_HMAC_SHA1` 以及 `AESCipher`。
实现可以参考 `pycryptodome` 后端的源码：

```python
# Written by DeepSeek

from Crypto.Cipher import AES
from Crypto.Protocol.KDF import PBKDF2
from Crypto.Hash import SHA1
from Crypto.Util import Counter


def pbkdf2_hmac_pycryptodome(pwd: bytes, length: int, salt: bytes, iterations: int) -> bytes:
    """
    使用 PyCryptodome 的 PBKDF2 实现
    """
    return PBKDF2(
        password=pwd,
        salt=salt,
        dkLen=length,
        count=iterations,
        hmac_hash_module=SHA1
    )


class PyCryptodomeAESCipher:
    __slots__ = ('_cipher',)

    def __init__(self, cipher):
        self._cipher = cipher

    @classmethod
    def get_cipher(cls, enc_key: bytes):
        """
        创建 AES-CTR 解密器
        """
        ctr = Counter.new(128, little_endian=True, initial_value=1)
        cipher = AES.new(enc_key, AES.MODE_CTR, counter=ctr)
        return cls(cipher)

    def update(self, encrypted_data: bytes) -> bytes:
        return self._cipher.decrypt(encrypted_data)

    def finalize(self) -> bytes:
        # pycryptodome 的 CTR 模式没有 finalize，直接返回空
        return b''


def install():
    from rtzip.zip_aes import install
    install(pbkdf2_hmac_pycryptodome, PyCryptodomeAESCipher)

```

#### 自定义解密后端（2）

也许你只想修改某些特定的算法，比如——把 PBKDF2_HMAC_SHA1 替换成标准库的实现，或者换用另一种加密库来做 Cipher。

这些功能通过两个装饰器实现：

- `pbkdf2_hmac_implement(func)`: 注册一个 PBKDF2_HMAC_SHA1 实现
- `aes_cipher_implement(cls)`: 注册一个 AESCipher 实现

它们并不强制成套出现，但是你需要确保两种实现都有被设置。

这两个函数都位于 `rtzip.zip_aes` 下。

```python
from rtzip.zip_aes import pbkdf2_hmac_implement, aes_cipher_implement


@pbkdf2_hmac_implement
def your_hmac_implement(pwd: bytes, length: int, salt: bytes, iterations: int):
    ...


@aes_cipher_implement
class YourAESCipher:
    @classmethod
    def get_cipher(cls, enc_key: bytes):
        return cls(...)

    def update(self, encrypted_data: bytes | bytearray | memoryview) -> bytes | bytearray | memoryview:
        ...

    def finalize(self) -> bytes | bytearray | memoryview:
        ...



```

在你实现了需要的功能后，调用 `rtzip.zip_aes.install()` 正式启用 WinZip-AES 支持。

这个函数将注册一个 `algorithm=0x63(99)` 的 `algorithm_handler`（参见：[自定义压缩算法支持](#自定义压缩算法支持)），
注意不要覆盖它（如果你想使用来自 `rtzip` 的解密实现的话）。

> [!TIP]
> `rtzip.zip_aes.install(pbkdf2_implement=None, cipher_implement=None)` 会自动设置传入的 implements，如果你已经使用装饰器的形式
> 设置了某个实现，可以在调用 `install` 时省略传入它

# 许可证

本项目采用 `BSD 3-Clause License`，全文参见 [LICENSE](LICENSE)

---
此 README 由 DeepSeek 协助整理