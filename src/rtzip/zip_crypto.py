from typing import AsyncGenerator, Any

from .models import CDEntry, LocalFileHeader

_crc_table = None


def get_crc_table():
    global _crc_table
    if _crc_table is None:
        table = [0] * 256
        for i in range(256):
            crc = i
            for j in range(8):
                if crc & 1:
                    crc = 0xEDB88320 ^ (crc >> 1)
                else:
                    crc >>= 1

            table[i] = crc & 0xFFFFFFFF

        _crc_table = table

    return _crc_table


def crc_update_keys(pwd: bytes, k0, k1, k2):
    for b in pwd:
        k0, k1, k2 = crc_update_keys_byte(b, k0, k1, k2)

    return k0, k1, k2


def crc_update_keys_byte(b: int, k0, k1, k2):
    t = get_crc_table()

    k0 = t[(k0 ^ b) & 0xFF] ^ (k0 >> 8)
    k0 &= 0xFFFFFFFF

    k1 = (k1 + (k0 & 0xFF)) & 0xFFFFFFFF
    k1 = (k1 * 0x08088405 + 1) & 0xFFFFFFFF

    k2 = t[(k2 ^ ((k1 >> 24) & 0xFF)) & 0xFF] ^ (k2 >> 8)
    k2 &= 0xFFFFFFFF

    return k0, k1, k2


def crc_keys_from_bytes(pwd: bytes):
    k0, k1, k2 = 0x12345678, 0x23456789, 0x34567890
    return crc_update_keys(pwd, k0, k1, k2)


def decrypt_byte(k2: int) -> int:
    temp = k2 | 3
    return ((temp * (temp ^ 1)) >> 8) & 0xFF


async def zipcrypto_wrapper(
        raw_data: AsyncGenerator[bytes, None],
        entry: CDEntry,
        header: LocalFileHeader,
        ctx: dict[str , Any]
) -> AsyncGenerator[bytes, None]:
    pwd = bytes(ctx['pwd'])

    buffer = bytearray()

    keys = crc_keys_from_bytes(pwd)

    crc = header.crc32

    # Get encrypted header
    while 12 > len(buffer):
        buffer.extend(await anext(raw_data))

    encrypted_header = buffer[:12]
    buffer = buffer[12:]

    for b in range(12):
        k = decrypt_byte(keys[2])
        dec = encrypted_header[b] ^ k

        keys = crc_update_keys_byte(dec, *keys)

        if b == 11 and dec != ((crc >> 24) & 0xFF):
            raise ValueError("Wrong Password")

    # 开始解密文件的数据
    data = bytearray()

    for b in buffer:
        k = decrypt_byte(keys[2])
        dec = b ^ k

        data.append(dec)

        keys = crc_update_keys_byte(dec, *keys)

    buffer.clear()
    del buffer

    yield bytes(data)

    async for chunk in raw_data:
        data.clear()

        for b in chunk:
            k = decrypt_byte(keys[2])
            dec = b ^ k

            data.append(dec)

            keys = crc_update_keys_byte(dec, *keys)

        yield bytes(data)

