import itertools
import re
import unittest
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

# 必须要提前 import，不然 ppmd 算法的自动安装会失败
import pyppmd  # type:ignore

from rtzip import RemoteZip, Context
from rtzip.builtin_sources import LocalFileSourceSync
from rtzip.wzaes_backends import tgcrypto_c_lectr_impl

tgcrypto_c_lectr_impl.install()


def bytes_diff(a, b):
    for x, y in zip(a, b):
        yield x, y, x != y


def show_diff(a, b, line_size=16):
    for i, batch in enumerate(itertools.batched(bytes_diff(a, b), line_size)):
        line_x = (k[0] for k in batch)
        line_y = (k[1] for k in batch)
        line_diff = [k[2] for k in batch]

        has_diff = bool(any(line_diff))

        if not has_diff:
            continue

        print(f'{i:<6}', end='')
        for j, x in enumerate(line_x):
            # color = colorama.Fore.RED if line_diff[j] else colorama.Fore.WHITE
            # print(f'{color}{hex(x):>04}', end=' ')
            print(f'{hex(x):>04}', end=' ')

        print('\n      ', end='')

        for j, y in enumerate(line_y):
            if line_diff[j]:
                # print(f'{colorama.Fore.GREEN}{hex(y):>04}', end=' ')
                print(f'{hex(y):>04}', end=' ')
            else:
                # print(colorama.Fore.WHITE, '-' * 4, end=' ', sep='')
                print('-' * 4, end=' ', sep='')

        print()
        # print(colorama.Fore.RESET)


project = Path(__file__).parent.parent
samples = project / 'samples'


@dataclass
class TestFile:
    path: Path
    src: Path
    crypto_mode: Literal['aes', 'norm', 'zipcrypto']
    algorithm: str


class TestAll(unittest.IsolatedAsyncioTestCase):
    filename_spec = re.compile(
        r'(?P<src_name>[a-zA-Z0-9.]+)'
        r'-zip-'
        r'(?P<crypto>aes|norm|zipcrypto)'
        r'-'
        r'(?P<algorithm>[a-zA-Z0-9]+)'
        r'\.zip'
    )

    def setUp(self) -> None:
        self.files: list[TestFile] = []
        for file in samples.iterdir():
            if file.is_file() and (match := self.filename_spec.fullmatch(file.name)):
                self.files.append(TestFile(
                    file,
                    src=samples / match.group('src_name'),
                    crypto_mode=match.group('crypto'),
                    algorithm=match.group('algorithm')
                ))

    async def test_all(self):
        for i, file in enumerate(self.files):
            with self.subTest(
                    f'Testing sample {i} '
                    f'({file.algorithm}, {file.crypto_mode}) '
                    f'of {len(self.files)}',
                    file=file.path.name,
                    crypto_mode=file.crypto_mode,
                    algorithm=file.algorithm
            ):
                async with RemoteZip(
                    LocalFileSourceSync(file.path),
                    Context(pwd='password'.encode())
                ) as rz:
                    data = await rz.read(rz.infolist[0].filename)

                    try:
                        self.assertEqual(data, file.src.read_bytes())
                    except AssertionError:
                        show_diff(data, file.src.read_bytes(), line_size=16)
                        raise

    async def test_context_merge(self):
        ctx1 = Context(eocd_scan_initial_chunk_size=128)
        ctx2 = Context(eocd_scan_initial_chunk_size=256)

        self.assertEqual((ctx1 | ctx2).eocd_scan_initial_chunk_size, 256)
        self.assertEqual((ctx2 | ctx1).eocd_scan_initial_chunk_size, 128)
