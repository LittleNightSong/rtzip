# Written by DeepSeek
import warnings

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC


def pbkdf2_hmac_impl(pwd: bytes, length: int, salt: bytes, iterations: int) -> bytes:
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA1(),
        length=length,
        salt=salt,
        iterations=iterations,
    )
    return kdf.derive(pwd)  # 注意：密码需要单独传递


class CryptographyAESCipher:
    __slots__ = ('_d', '_enc_key', '_initial_value', '_c', '_b')

    def __init__(self, enc_key):
        self._enc_key = enc_key
        self._c = 1
        self._b = bytearray()

    @classmethod
    def get_cipher(cls, enc_key: bytes):
        return cls(enc_key)

    def update(self, encrypted_data: bytes) -> bytes:
        # print(f"拼接：{len(self._b)} + {len(encrypted_data)} = {len(self._b) + len(encrypted_data)}")
        self._b.extend(encrypted_data)
        buffer = bytearray()

        for offset in range(0, len(self._b), 16):
            data = self._b[offset:offset + 16]

            d = Cipher(
                algorithms.AES(self._enc_key),
                modes.CTR(self._c.to_bytes(16, 'little'))
            ).decryptor()

            if len(data) == 16:
                self._c += 1
                buffer.extend(d.update(data))

            else:
                self._b = self._b[offset:]
                # print("裁剪：", len(self._b))
                break

        return buffer

    def finalize(self) -> bytes:
        d = Cipher(
            algorithms.AES(self._enc_key),
            modes.CTR(self._c.to_bytes(16, 'little'))
        ).decryptor()

        k = d.update(self._b)
        print("K", k)
        return k


def install():
    warnings.warn(
        "The 'cryptography' backend is deprecated and will be removed in version 1.0. "
        "We strongly recommend switching to 'pycryptodome' for better performance. "
        "To migrate, import `rtzip.backends.pycryptodome_impl` and call `install()`.",
        DeprecationWarning,
        stacklevel=2
    )

    from rtzip.zip_aes import install
    install(pbkdf2_hmac_impl, CryptographyAESCipher)
