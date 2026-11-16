import tgcrypto_lectr
import hashlib


def pbkdf2_hmac_impl(pwd, length, salt, iterations):
    return hashlib.pbkdf2_hmac(
        'sha1', pwd, salt, iterations, length
    )


class TGCryptoCipher:
    def __init__(self, enc_key):
        self.ctx = tgcrypto_lectr.CTRContext(
            enc_key,
            (1).to_bytes(16, byteorder='little'),
            # 1
            # endian='big'
        )

    @classmethod
    def get_cipher(cls, enc_key):
        return cls(enc_key)

    def update(self, data):
        return self.ctx.update(data)

    def finalize(self):
        return b''


def install(override: bool = False):
    from rtzip.wz_aes import install
    install(pbkdf2_hmac_impl, TGCryptoCipher, override=override)
