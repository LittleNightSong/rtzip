# Written by DeepSeek

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
    __slots__ = ('_d', )

    def __init__(self, decryptor):
        self._d = decryptor

    @classmethod
    def get_cipher(cls, enc_key: bytes):
        cipher = Cipher(algorithms.AES(enc_key), modes.CTR((1).to_bytes(16, 'little')))
        decryptor = cipher.decryptor()
        return cls(decryptor)

    def update(self, encrypted_data: bytes) -> bytes:
        return self._d.update(encrypted_data)

    def finalize(self) -> bytes:
        return self._d.finalize()


def install():
    from rtzip.zip_aes import install
    install(pbkdf2_hmac_impl, CryptographyAESCipher)
