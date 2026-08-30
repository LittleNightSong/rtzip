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

### 直接使用

你需要先定义一个自己的数据源，再将其传递给 `RemoteZip`

数据源需要包含三个方法：

- `read_range(offset, length) -> bytes`: 随机读取某个数据段
- `read_range_stream(offset, length) -> AsyncGenerator[bytes | bytearray | memoryview, None]`: 流式随机读取某个数据段
- `get_total_size() -> int`: 获取源的总大小（用于 Zip-EOCD 查找）

```python
from rtzip import RemoteZip, DataSource

import niquests  # 如果你使用 niquests 作为 http 依赖的话


class HTTPSource(DataSource):
    def __init__(self, url, session=None):
        self.url = url
        self.session = session or niquests.AsyncSession

    async def _request_range(self, offset, length):
        resp = await self.session.get(self.url, headers={'Range': f"bytes={offset}-{offset + length - 1}"}, stream=True)
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

创建 `rtzip.RemoteZip` 实例：

```python
rz = RemoteZip(HTTPSource(url=..., session=...))
```

元数据拉取：

```python

# 显式拉取 EOCD 信息
eocd = await rz.fetch_zip_info()

# 拉取中央目录的信息（文件列表）
files = await rz.fetch_file_list()
# 返回一个包含 `rtzip.models.CDEntry` 对象的列表
# 如果还未拉取 EOCD，则将自动拉取 EOCD

# 获取本地文件头信息
header = await rz.fetch_file_header(filename=...)
# 返回一个 `rtzip.models.LocalFileHeader` 对象

# 你也可以通过 `rtzip.models.CDEntry` 对象拉取本地文件头，这样可以跳过文件名查找
header = await rz.fetch_file_header(entry=...)

# 类似的函数： .header(path: bytes)
header = await rz.header(b'<filename>')
# 这个函数的不同在于它会自动规范化路径

```

元数据操作：

```python

# 通过文件名查找其对应的 CDEntry 对象（直接依赖传入的原始路径）
entry = rz.find_file_entry(b'<filename>')

# 更简洁、智能的查找 CDEntry 对象（路径会经过 [normpath](src/rtzip/path_methods.py) 规范化）
# 但是缺少了对 file_mapping 映射表的复用，可能带来性能损失

entry = rz.entry(b'<filename>')

# 获取文件存在性

if rz.exists(b'<filename>'):
    ...
else:
    ...

# 获取指定目录下的子项（基于文件名前缀且不包含目录本身）
files: list[bytes] = rz.listdir(b'<filename>')

# 符号链接解析

target = await rz.resolve(b'<filename>')

# 模式匹配
files = rz.rglob(b"*.py")

entries = rz.rglob_entries(b"*.py")

# 大小获取

original_size = rz.original_size(b'<filename>')
compressed_size = rz.compressed_size(b'<filename>')

```

文件读取：

```python
data = await rz.read(b'<filename>')

async for i in rz.stream(b'<filename>'):
    print(i)

```

> [!TIP]
> 更具体的文档参见方法和类的文档注释

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

启用很简单，rtzip 提供了两种不同的加密后端实现：`pycryptodome` 以及 `cryptography`
但是我们更建议使用 `pycryptodome`，因为 cryptography 的某些局限性，项目不得不在性能上做出妥协

#### pycryptodome

```python
from rtzip.wzaes_backends import pycryptodome_impl

pycryptodome_impl.install()
```

#### cryptography (已弃用)

如果没有 `cryptography` 的强制需求，我们更建议使用 `pycryptodome`

```python
from rtzip.wzaes_backends import cryptography_impl

cryptography_impl.install()

```

#### 自定义解密后端

自定义后端要求你提供两个方法的实现：`PBKDF2_HMAC_SHA1` 以及 `AESCipher`
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

也许你只想修改某些特定的算法，比如——把 PBKDF2_HMAC_SHA1 替换成标准库的实现，或者换用另一种加密库来做 Cipher

这些功能通过两个装饰器实现：

- `pbkdf2_hmac_implement(func)`: 注册一个 PBKDF2_HMAC_SHA1 实现
- `aes_cipher_implement(cls)`: 注册一个 AESCipher 实现

它们并不强制成套出现，但是你需要确保两种实现都有被设置

这两个函数都位于 `rtzip.zip_aes` 下

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

在你实现了需要的功能后，调用 `rtzip.zip_aes.install()` 正式启用 WinZip-AES 支持

这个函数将注册一个 `algorithm=0x63(99)` 的 `algorithm_handler`（参见：[自定义压缩算法支持](#自定义压缩算法支持)
），注意不要覆盖它（如果你想使用来自 `rtzip` 的解密实现的话

# 许可证

本项目采用 `BSD 3-Clause License`，全文参见 [LICENSE](LICENSE)
