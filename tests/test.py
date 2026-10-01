import asyncio
import itertools
import os
from pathlib import Path
from typing import AsyncGenerator

from niquests import AsyncSession

from rtzip import RemoteZip
from rtzip.remotezip import DataSource
from rtzip.wzaes_backends import pycryptodome_impl


class URLSource(DataSource):
    def __init__(self, url: str, session: AsyncSession):
        self.session = session
        self.url = url

    async def get_total_size(self) -> int:
        resp = await self.session.head(self.url)
        resp.raise_for_status()

        return int(resp.headers["Content-Length"])

    async def read_range(self, offset, length) -> bytes | bytearray | memoryview:
        resp = await self.session.get(self.url, headers={
            'Range': f'bytes={offset}-{offset + length - 1}'
        }, stream=True)
        resp.raise_for_status()
        if resp.status_code == 206:
            data = await resp.content
            assert data and len(data) == length
            await resp.close()
            return data
        else:
            await resp.close()
            raise RuntimeError()

    async def read_range_stream(self, offset, length) -> AsyncGenerator[bytes | bytearray | memoryview, None]:
        resp = await self.session.get(
            self.url,
            headers={
                'Range': f'bytes={offset}-{offset + length - 1}'
            },
            stream=True
        )
        resp.raise_for_status()
        if resp.status_code == 206:
            print("Start reading")
            async for i in await resp.iter_content(-1):
                yield i

            print("End reading")
            await resp.close()
            print("Closing")
        else:
            await resp.close()
            raise RuntimeError()


class LocalFileSource(DataSource):
    def __init__(self, filename):
        self.fp = open(filename, 'rb')

    async def get_total_size(self) -> int:
        return os.fstat(self.fp.fileno()).st_size

    async def read_range(self, offset, length) -> bytes | bytearray | memoryview:
        self.fp.seek(offset)
        return self.fp.read(length)

    async def read_range_stream(self, offset, length) -> AsyncGenerator[bytes | bytearray | memoryview, None]:
        self.fp.seek(offset)
        chunk_size = 8192

        while length > 0:
            read_size = min(chunk_size, length)

            # print(f"Prepared to read {read_size} bytes, size elapsed: {length}")

            yield self.fp.read(read_size)
            length -= read_size


def bytes_diff(a, b):
    for x, y in zip(a, b):
        yield x, y, x != y


async def main():
    print("Start")

    pycryptodome_impl.install()
    # cryptography_impl.install()

    # filename = "E:/.projects/rtzip/dist/rtzip-0.3.1-py3-none-any.whl"
    # filename = r"D:\images.zip"
    filename = r"D:\images_zipaes.zip"
    source = LocalFileSource(filename)
    rz = RemoteZip(source)

    print(await rz.lowerlevel_fetch_eocd(max_cnt=1))
    print(await rz.lowerlevel_fetch_central_directory())

    file = b"IMG_10012.png"

    data = await rz.read(file, pwd='114514'.encode('utf-8'))
    original_file = r"D:\DataSet\test\images\IMG_10012.png"

    o_data = Path(original_file).read_bytes()

    result = data == o_data
    print(f"Result: {result} ({len(data)} bytes compared with {len(o_data)})")

    if not result:
        line_size = 16
        for i, batch in enumerate(itertools.batched(bytes_diff(data, o_data), line_size)):
            line_x = (i[0] for i in batch)
            line_y = (i[1] for i in batch)
            line_diff = [i[2] for i in batch]

            has_diff = bool(any(line_diff))

            if not has_diff:
                continue

            print(f'{i:<6}', '. '.join(map(lambda x: format(x, '^5'), line_x)))
            print(' ' * 6, '. '.join(map(lambda x: format(x, '^5'), line_y)))
            print(
                f'{str(has_diff):<6}',
                '. '.join(map(
                    lambda x: format('^', '^6') if x else '      ',
                    line_diff
                ))
            )

            input()


if __name__ == '__main__':
    asyncio.run(main())
