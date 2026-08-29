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