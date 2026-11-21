import asyncio
import difflib
import itertools
import re
from pathlib import Path
from typing import AsyncGenerator

import colorama.ansi
import pyzipper
from niquests import AsyncSession
from rich import print

from rtzip import RemoteZip, DataSource
from rtzip.builtin_sources import LocalFileSourceSync
from rtzip.context import Context
from rtzip.wzaes_backends import tgcrypto_c_lectr_impl


class URLSource(DataSource):
    def __init__(self, url: str, session: AsyncSession):
        self.session = session
        self.url = url

    async def get_total_size(self) -> int:
        resp = await self.session.head(self.url)
        resp.raise_for_status()

        return int(resp.headers["Content-Length"])

    async def _request(self, offset, length):
        resp = await self.session.get(self.url, headers={
            'Range': f'bytes={offset}-{offset + length - 1}'
        }, stream=True)
        resp.raise_for_status()
        if resp.status_code == 206:
            # 检查服务器的区间
            content_range = resp.headers['Content-Range']
            match = re.match(r'(\d+)-(\d+)', content_range)
            if not match:
                raise RuntimeError()

            start, end = int(match.group(1)), int(match.group(2))
            if end - start + 1 != length:
                raise RuntimeError()

            return resp
        else:
            await resp.close()
            raise RuntimeError()

    async def read_range(self, offset, length) -> bytes | bytearray | memoryview:
        return await (await self._request(offset, length)).content

    async def read_range_stream(self, offset, length) -> AsyncGenerator[bytes | bytearray | memoryview, None]:
        resp = await self._request(offset, length)

        gen = await resp.iter_content()
        async for chunk in gen:
            yield chunk


def bytes_diff(a, b):
    for x, y in zip(a, b):
        yield x, y, x != y


def show_diff(a, b, line_size=16):
    from builtins import print
#
#     sm = difflib.SequenceMatcher(None, a, b)
#     for op, i1, i2, j1, j2 in sm.get_opcodes():
#         match op:
#             case 'equal':
#                 print()
#
#             case ''

    for i, batch in enumerate(itertools.batched(bytes_diff(a, b), line_size)):
        line_x = (k[0] for k in batch)
        line_y = (k[1] for k in batch)
        line_diff = [k[2] for k in batch]

        has_diff = bool(any(line_diff))

        if not has_diff:
            continue

        print(f'{i:<6}', end='')
        for j, x in enumerate(line_x):
            color = colorama.Fore.RED if line_diff[j] else colorama.Fore.WHITE
            print(f'{color}{hex(x):>04}', end=' ')

        print('\n      ', end='')

        for j, y in enumerate(line_y):
            if line_diff[j]:
                print(f'{colorama.Fore.GREEN}{hex(y):>04}', end=' ')
            else:
                print(colorama.Fore.WHITE, '-' * 4, end=' ', sep='')

        print(colorama.Fore.RESET)


def compare_bytes(data, fact):
    result = data == fact

    print(f"Equal? {result} ({len(data)} bytes compared with {len(fact)})")

    if not result:
        show_diff(data, fact)

    return result


async def compare_file(rz: RemoteZip, arcname: bytes, filename: Path):
    arc = await rz.read(arcname)
    return compare_bytes(arc, filename.read_bytes())


def comparing_pyzipper(achieve, arcname, realfile, pwd=None):
    print("PyZipper")
    with pyzipper.AESZipFile(achieve, 'r') as zipper:
        zipper.pwd = pwd

        data = zipper.read(arcname)
        original = Path(realfile).read_bytes()

        compare_bytes(data, original)


async def main():
    tgcrypto_c_lectr_impl.install()
    # pycryptodome_impl.install(override=True)

    pwd = b'114514'

    root = Path(__file__).parent.parent
    samples = root / 'samples'

    # zipfile = samples / 'random.bin.zip'
    zipfile = samples / 'random.bin-zip-norm-store.zip'

    source = LocalFileSourceSync(zipfile)
    rz = await RemoteZip.open(source, Context(pwd=pwd))

    file = b"/random.bin"
    original_file = samples / 'random.bin'

    comparing_pyzipper(zipfile, file.decode()[1:], original_file, pwd)

    print("Rtzip")
    await compare_file(rz, file, original_file)

    # print(rz.info(file)._entry.last_modified_time)
    # print(rz.info(file)._entry.last_modified_date)

    # TODO: Find why data error


if __name__ == '__main__':
    asyncio.run(main())
