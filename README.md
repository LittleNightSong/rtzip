from rtzip import RemoteZipfrom rtzip import RemoteZipfrom rtzip import RemoteZip

# Rtzip

_一个以最小的数据量提取远程 zip 文件内容的库_

## 特点

- 不与任何远程数据源耦合
- 依赖简单（只有用于解析二进制结构的 `obstruct`）、纯 Python
- 兼容标准 zip 格式、ZIP64 扩展

## 安装

```commandline
pip install rtzip
```

## 用法

### 准备数据源

你需要先定义一个自己的数据源，再将其传递给 `RemoteZip`。

数据源需要包含五个方法：

- `read_range(offset, length) -> bytes | bytearray | memoryview`: 随机读取某个数据段
- `read_range_stream(offset, length) -> AsyncGenerator[bytes | bytearray | memoryview, None]`: 流式随机读取某个数据段
- `get_total_size() -> int`: 获取源的总大小（用于 Zip-EOCD 查找）
- `setup() -> None`: 初始化数据源
- `close() -> None`: 关闭数据源

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
        # 实际使用的时候，必须校验服务器的相应长度和我们期望的长度一致，否则 rtzip 将无法正常解析
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
    
    async def close(self):
        await self.session.close()

```

> [!WARNING]
> rtzip 的逻辑代码默认数据源是可信的，不会对数据源返回的数据长度做任何校验。编写数据源时请务必保证返回的数据大小等于请求的大小
> 建议检查行为越早越好

### 创建实例

```python
rz = RemoteZip(HTTPSource(url=..., session=...)))
```

`rtzip` 在实现上分为两层：

- **High-level**：`RemoteZip`，提供以规范化路径为核心的便捷接口，同时通过 `lowlevel` 属性暴露了底层 `LowLevelRemoteZip` 对象。
- **Low-level**：`LowLevelRemoteZip`，提供读取远程 zip 文件的底层方法，全部为异步，直接操作原始路径，不自动做路径规范化，也不自动维护规范化映射表。

---

### High-level API

High-level API 以规范化路径（[normpath](src/rtzip/path_methods.py)）为统一入口，适合绝大多数使用场景。
建议先调用一次 `init()` 完成元数据初始化，之后大部分查询方法都可以同步调用。

#### 初始化与一次性元数据加载

```python
# 一次性拉取 EOCD、中央目录并建立文件名 -> CDEntry 映射
await rz.init()

# 或者在创建实例时使用 `.open(src, ctx)` 类方法，它会自动执行初始化
rz = RemoteZip.open(HTTPSource(url=..., session=...))

# 获取所有的文件名（bytes）列表
names: list[bytes] = rz.namelist()

# 获取「规范化文件名 -> CDEntry」的映射
mapping = rz.namemapping()

# 手动重建映射表（在中央目录变更后调用）
rz.build_mapping()

```

> [!TIP]
> 大多数 `RemoteZip` 的元数据操作（`entry`、`exists`、`listdir` 等）都要求实例已初始化完毕，
> 或者手动调用过了 `.lowlevel.fetch_central_directory()` 方法
> 一般来说，通过 `.open(src, ctx)` 和调用了 `.init()` 方法的 RemoteZip 实例都可以直接执行元数据查询

#### 元数据查询

```python

# 通过规范化路径查找 CDEntry
entry = rz.entry(b'data/file')

# 以下写法是等价的，重复的斜杠和位于开头的斜杠会在规范化路径时自动消除
# entry = rz.entry(b'/data/file')
# entry = rz.entry(b'//data/file')
# entry = rz.entry(b'/data//file')

# 文件存在性
if rz.exists(b'<filename>'):
    ...
else:
    ...

# 获取指定目录下的子项（基于文件名前缀且不包含目录本身）
files: list[bytes] = rz.listdir(b'<filename>')

# 获取指定目录下子项的 CDEntry 对象
file_entries: list[CDEntry] = rz.listdir_entries(b'<filename>')

# 模式匹配
for i in rz.rglob(b"*.py"):
    ...

for entry in rz.rglob_entries(b"*.py"):
    ...

# 大小获取
original_size = rz.original_size(b'<filename>')
compressed_size = rz.compressed_size(b'<filename>')

# 拉取本地文件头
header = await rz.header(b'<filename>')

# 获取文件的本地文件头起始偏移量
header_offset = rz.header_offset(b'<filename>')

# 获取文件内容的起始偏移量
start, length = rz.content_range(b'<filename>')

# 同时获取文件内容偏移量和压缩算标识
start, length, algorithm = rz.range_and_algorithm(b'<filename>')

# 同时获取文件内容偏移量和数据流处理器
start, length, handler = rz.range_and_handler(b'<filename>')
# 这个处理器只接受一个异步数据流生成器，返回一个异步数据流生成器，生成处理和解压后的数据
# Handler 在调用期间会自动拉取 Local File Header

```

#### 文件读取

```python

# 直接将一个文件的解压后原始数据加载进内存
data: bytearray = await rz.read(b'<filename>')


# 流式读取和解压文件
async for chunk in rz.stream(b'<filename>'):
    print(chunk)

# 直接以文本形式读取（底层仍然走 read）
text = await rz.read_text(b'<filename>', encoding='utf-8', errors='strict')
```


#### 上下文信息

rtzip 的上下文对象允许控制：

##### General

- `pwd`: 在需要解密文件时提供的密码

##### EOCD Scan

- `eocd_scan_initial_chunk_size`: 扫描 EOCD 时初始的块大小
- `eocd_scan_max_retry`: 最大重新拉取数据块次数
- `eocd_scan_rechunk_factor`: 在重新拉取数据块时新的块大小系数
- `eocd_scan_use_cache`: 是否允许复用缓存在实例中的 EOCD 信息

##### Extra

当你为 `Context` 对象赋值新的属性的时候，配置会被放入 `.extras` 字典中
当你访问一个 `Context` 不存在的属性的时候，会尝试先在 `.extras` 中查找，再抛出错误

> [!NOTE]
> 上下文对象的配置能力将会随着新版本的发布而继续拓展

##### 上下文的合并

你可以将通过 `|` 运算符合并两个 `Context` 对象，这样会产生一个新对象

你也可以通过 `Context | dict` 的运算将上下文和字典配置合并，
字典中在 `Context` 中声明的部分会合并到属性中，额外的部分会合并到 `.extras` 字典中


> [!TIP]
> 更具体的文档参见方法和类的文档注释

---

### Low-Level API

待补充

---

### 自定义压缩算法支持

```python
# 示例：扩展 PPMd 算法支持（基于 ppmd 库）
import ppmd
from rtzip import algorithm_handler


@algorithm_handler(0x000A)
async def ppmd_wrapper(raw_generator, entry, header, ctx):
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
# context 是调用 `read` 等方法时传入的 context 与 `RemoteZip` 实例级上下文的合并结果

```

### 启用加密文件支持

rtzip 提供了基于`cryptography`和`tgcrypto-c-lectr`的加密支持，同时也允许自定义加密后端。

#### pycryptodome

```python
from rtzip.wzaes_backends import pycryptodome_impl

pycryptodome_impl.install()
```

rtzip 还有一个基于 `cryptography` 的加密支持，但由于其硬性约束，使用对性能影响极大


#### Tgcrypto-c-lectr

```python
from rtzip.wzaes_backends import tgcrypto_c_lectr_impl

tgcrypto_c_lectr_impl.install()
```

> [!NOTE]
> [`tgcrypto-c-lectr`](https://github.com/LittleNightSong/tgcrypto-c-lectr) 
> 是基于 'tgcrypto-c' 衍生的项目， 其修改了 `CTR` 的实现使计数器变为 `WinZip AES` 所需的小端序。

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
    from rtzip.wz_aes import install
    install(pbkdf2_hmac_pycryptodome, PyCryptodomeAESCipher)

```

#### 自定义解密后端（2）

也许你只想修改某些特定的算法，比如——把 PBKDF2_HMAC_SHA1 替换成标准库的实现，或者换用另一种加密库来做 Cipher。

这些功能通过两个装饰器实现：

- `pbkdf2_hmac_implement(func)`: 注册一个 PBKDF2_HMAC_SHA1 实现
- `aes_cipher_implement(cls)`: 注册一个 AESCipher 实现

它们并不强制成对出现，但是你需要确保两种实现都有被设置。

这两个函数都位于 `rtzip.zip_aes` 下。

```python
from rtzip.wz_aes import pbkdf2_hmac_implement, aes_cipher_implement


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