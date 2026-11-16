from .errors import NotEnoughDataError
from .remotezip import DataSource
import os


class LocalFileSourceSync(DataSource):
    def __init__(self, file):
        self.file = file
        self.fp = open(file, 'rb')
        self.chunk_size = 1024 * 1024

    async def read_range_stream(self, offset, length):
        self.fp.seek(offset)
        size = 0
        while True:
            elapsed = length - size

            if elapsed < self.chunk_size:
                data = self.fp.read(elapsed)

                if len(data) < elapsed:
                    raise NotEnoughDataError(length, size + len(data))

                yield data
                return

            else:
                data = self.fp.read(self.chunk_size)

                if len(data) < self.chunk_size:
                    raise NotEnoughDataError(length, size + len(data))

                yield data

            size += len(data)

    async def read_range(self, offset, length):
        self.fp.seek(offset)
        data = self.fp.read(length)
        if len(data) < length:
            raise NotEnoughDataError(length, len(data))
        return data

    async def get_total_size(self):
        return os.fstat(self.fp.fileno()).st_size

    async def close(self):
        self.fp.close()
