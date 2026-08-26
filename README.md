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


async def main():
    url = ...
    rz = RemoteZip(HTTPSource(url))
    rz.fetch_zip_info()  # 加载文件的 EOCD，返回一个 EOCD 获取 Zip64EOCD 对象
    rz.fetch_file_list()  # 加载文件的中央目录，返回一个包含 CDEntry 对象的列表
    # 这些数据如果没有即时接收，也会被缓存在 rz.eocd 和 rz.files 中

    rz.build_mapping()  # 构建文件名 -> Entry 对象的映射表，方便查表
    # 返回映射表本身，同时缓存在 rz.file_mapping 中

    # 一次性读取一个文件的全量数据
    data = await rz.load_single_file(b'filename')

    # 流式读取一个文件的数据
    buffer = bytearray()
    async for i in rz.stream_single_file(b'filename'):
        buffer.extend(i)

    # 获取一个文件的元数据信息
    entry = rz.find_file_entry(b'filename')
    # 可能抛出 FileNotFoundError

    # 从文件名拉取一个文件对应的本地文件头
    header = await rz.fetch_file_header(filename=b'filename')

    # 也可以从已有的 entry 对象拉取
    # 但这时你要自己保证这个 entry 对象是有效的
    # 将返回一个 LocalFileHeader 对象
    header = await rz.fetch_file_header(entry=entry)


if __name__ == '__main__':
    import asyncio

    asyncio.run(main())

```

### 自定义压缩算法处理

```python
# 示例：扩展 PPMd 算法支持（需要 pip install ppmd）
import ppmd
from rtzip import algorithm_handler


@algorithm_handler(0x000A)
async def ppmd_wrapper(raw_generator):
    # PPMd 流式解压（API 取决于具体库）
    decomp = ppmd.Ppmd7Decompressor()
    async for chunk in raw_generator:
        yield decomp.decompress(chunk)


# 这之后使用 RemoteZip 时遇到 ppmd 算法会自动应用你的包装器

# 如果想要重写某个内置的包装器
# 你需要指定 `override=True`，否则程序将会阻止覆盖行为
@algorithm_handler(0x0008, override=True)
async def your_deflate_wrapper(raw_generator):
    ...

```

> [!TIP]
> 本库无计划新功能，若是让自行添加功能，可克隆仓库并重写 `rtzip/remotezip.py` 下的代码
> 大多数库的功能已完成并可以直接投入使用
