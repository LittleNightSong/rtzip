import dataclasses
import hashlib
import hmac
from typing import Protocol

from obstruct import Struct, u16, u8

from . import BadWinzipAESData, HMACError
from .algorithm_register import algorithm_handler, is_algorithm_handlier_available, get_algorithm_handler
from .errors import UnsupportedAlgorithmError, WrongPasswordError
from .stream_io import StreamIO


@dataclasses.dataclass(slots=True)
class AESExtraData(Struct):
    vendor_version: u16
    vendor_id: u16  # 2 = AE-1, 3 = AE-2
    strength: u8  # 1=128bits, 2=192bits, 3=256bits
    compression: u16
    # mac_length: u16  # only available on AE-2


# 这些方法要求用户在初始化的时候设置，否则无法解密

_decrypt_helpers = {}


class PBKDF2_HMAC_FunctionType(Protocol):
    def __call__(self, pwd: bytes, length: int, salt: bytes, iterations: int) -> bytes:
        """
        PBKDF2-HMAC-SHA1 实现函数

        :param pwd: 解密使用的密码
        :param length: 派生密钥的总长度
        :param salt: 密钥盐
        :param iterations: 迭代次数
        :return: 一个字节串，表示派生的密钥
        """
        ...


from typing import Protocol, runtime_checkable


@runtime_checkable
class AESDecryptor(Protocol):
    """
    AES 计数器模式 (CTR) 解密器协议。

    该协议定义了 WinZip AES 加密解密所需的核心接口，
    任何实现了该协议的类型都可以用于 AES-CTR 模式解密。
    """

    @classmethod
    def get_cipher(cls, enc_key: bytes, /) -> 'AESDecryptor':
        """
        创建并返回一个 AES-CTR 模式解密器实例。

        该方法是工厂方法，用于初始化解密器。实现类应确保
        返回的实例处于可解密状态，并准备好处理数据。

        :param enc_key: AES 加密密钥，长度必须为 16 (AES-128)、
                        24 (AES-192) 或 32 (AES-256) 字节
        :param iv: 初始化向量 (Initialization Vector)，固定 16 字节，
                   作为 AES-CTR 模式的计数器初始值
        :return: 初始化完成的 AESCipher 实例
        :raises ValueError: 当 enc_key 或 iv 长度不符合要求时抛出

        :example:
            >>> cipher = AESDecryptor.get_cipher(
            ...     enc_key=b'\\x00' * 32,
            ...     iv=b'\\x00' * 16
            ... )
        """
        ...

    def update(self, encrypted_data: bytes, /) -> bytes:
        """
        解密一部分数据，返回解密后的明文数据。

        该方法可以多次调用，每次处理一个数据块。解密状态会
        在调用之间保持，以支持流式解密。如果 encrypted_data
        为空，返回空字节串。

        :param encrypted_data: 需要解密的密文数据块，长度任意
        :return: 解密后的明文数据块，长度与输入相同
        :raises ValueError: 当解密器未正确初始化或处于无效状态时抛出

        :example:
            >>> cipher = AESDecryptor.get_cipher(enc_key, iv)
            >>> plaintext1 = cipher.update(ciphertext[:1024])
            >>> plaintext2 = cipher.update(ciphertext[1024:])
            >>> plaintext = plaintext1 + plaintext2
        """
        ...

    def finalize(self, /) -> bytes:
        """
        完成解密过程，返回剩余的明文数据并清理资源。

        该方法应在所有数据通过 update() 处理完毕后调用。它会
        处理可能存在的剩余数据（对于 CTR 模式通常为空），
        并执行必要的清理工作（如清除敏感数据）。调用后，解密器
        实例应被视为不可用状态，不应再调用 update()。

        :return: 剩余的明文数据（对于 AES-CTR 通常为空字节串）
        :raises ValueError: 当解密器处于无效状态或已被 finalize 时抛出

        :example:
            >>> cipher = AESDecryptor.get_cipher(enc_key, iv)
            >>> plaintext = cipher.update(ciphertext)
            >>> plaintext += cipher.finalize()
        """
        ...


def pbkdf2_hmac_implement[T: PBKDF2_HMAC_FunctionType](func: T) -> T:
    """
    注入 pbkdf2-hmac 的实现，库为了轻量，并没有选择依赖任何已有的加密库，而是将选择留给开发者
    函数接受 2 个参数：
        1. length: int 这个参数指定了派生密钥的总长度
        2. salt: 密钥的 salt

    返回一个字节串，表示派生密钥的值

    :param func:
    :return:
    """
    _decrypt_helpers['pbkdf2-hmac'] = func
    return func


def aes_cipher_implement[T: type[AESDecryptor]](cls: T) -> T:
    _decrypt_helpers['aes-cipher'] = cls
    return cls


def call_pbkdf2_hmac(pwd: bytes, length: int, salt: bytes, iterations: int):
    return _decrypt_helpers['pbkdf2-hmac'](pwd, length, salt, iterations)


def get_cipher(enc_key: bytes) -> AESDecryptor:
    return _decrypt_helpers['aes-cipher'].get_cipher(enc_key)


class CipherWrapper:
    def __init__(self, chipher: AESDecryptor, hmac_obj: hmac.HMAC):
        self._c = chipher
        self._h = hmac_obj

    def __call__(self, data=None):
        if data is not None:
            self._h.update(data)
            return self._c.update(data)
        else:
            return self._c.finalize()


def get_decrypt_info(pwd, key_len, salt):
    key_material = call_pbkdf2_hmac(pwd, 2 * key_len + 2, salt, 1000)
    enc_key = key_material[:key_len]
    hmac_key = key_material[key_len:key_len * 2]
    checksum = key_material[-2:]

    return enc_key, hmac_key, checksum


# @algorithm_handler(0x0063)
async def zip_aes_stream_decryptor(
        io,
        compressed_size,
        aes_extra,
        raw_aes_extra, pwd
):
    # print(aes_extra)
    # print(raw_aes_extra, len(raw_aes_extra), aes_extra.__cstruct__.size)

    mac_length = 10

    if aes_extra.vendor_version == 3:
        offset = aes_extra.__cstruct__.size
        mac_length = int.from_bytes(
            raw_aes_extra[offset:offset + 2], 'little'
        )

    key_len = (0, 16, 24, 32)[aes_extra.strength]
    salt_len = (0, 8, 12, 16)[aes_extra.strength]

    enc_header_len = salt_len + 2

    encrypted_data_len = compressed_size - enc_header_len - mac_length
    if encrypted_data_len < 0:
        raise BadWinzipAESData(
            f'Invalid compressed_size: {compressed_size} '
            f'(enc_header_len={enc_header_len}, mac_length={mac_length})'
        )

    enc_header = await io.read_exactly(enc_header_len)

    salt = enc_header[:salt_len]
    quick_check = enc_header[salt_len: salt_len + 2]

    # 获取派生密钥
    enc_key, hmac_key, checksum = get_decrypt_info(pwd, key_len, bytes(salt))

    # print(f"Vendor Version: {aes_extra.vendor_version}")
    # print(f"Strenth: {aes_extra.strength}")
    # print(f"User Password: {pwd}")
    # print(f"Zip Salt: {salt} ({len(salt)})")
    # print(F"HMAC Length: {mac_length}")
    # print(F"ENC Key: {enc_key}")
    # print(F"HMAC Key: {hmac_key}")
    # print(F"Check: {checksum} vs. {quick_check}")

    if checksum != quick_check:
        raise WrongPasswordError()

    # 流式解密
    # 同时需要校验

    c = get_cipher(enc_key)
    h = hmac.new(
        key=hmac_key,
        digestmod=hashlib.sha1
    )

    cipher = CipherWrapper(c, h)

    size = 0
    buf = bytearray()
    stream = io.raw_stream()
    async for chunk in stream:  # 开始遍历真正的数据
        size += len(chunk)  # 累计大小

        if size <= encrypted_data_len:  # 目前接受的数据还不满
            # 正常迭代、解密和更新校验值
            yield cipher(chunk)
        else:  # 发现已接收大小超过了期望接受的大小
            pos = -(size - encrypted_data_len)

            yield cipher(chunk[:pos])
            buf.extend(chunk[pos:])
            # 把剩余数据放进 buffer
            # 某些极端情况下可能会产生紧跟其后的 MAC 差几个字节的问题
            # 我们需要在后面把数据补上
            break

    await stream.aclose()

    if x := cipher(None):
        yield x

    # 补齐 MAC 数据
    if len(buf) < mac_length:
        buf.extend(await io.read_exactly(mac_length - len(buf)))

    # print(f"Elpasted Size: {len(buf)}")

    digest = h.digest()[:mac_length]
    if not hmac.compare_digest(digest, buf):
        raise HMACError('HMAC mismatch')

    if await io.read(1):
        raise BadWinzipAESData("Extra data at end of the stream.")


def zip_aes_wrapper(raw_data, entry, header, ctx):
    assert ctx.get('pwd')

    assert _decrypt_helpers.get(
        'aes-cipher'
    ), "You didn't register a AESCipher type, please register it by `aes_cipher_implement` decorator first"
    assert _decrypt_helpers.get(
        'pbkdf2-hmac'
    ), "You didn't register a PBKDF2-HMAC function, please register it by `pbkdf2_hmac_implement` decorator first"

    raw_aes_extra = header.extra_fields[0x9901]
    aes_extra = AESExtraData.unpack(raw_aes_extra)

    # 获取解密后的压缩格式
    algorithm = aes_extra.compression

    if not is_algorithm_handlier_available(algorithm):
        raise UnsupportedAlgorithmError(algorithm)

    return get_algorithm_handler(algorithm)(
        zip_aes_stream_decryptor(StreamIO(raw_data), entry.compressed_size, aes_extra, raw_aes_extra, ctx.get('pwd')),
        entry, header, ctx
    )


def install(pbkdf2_hmac: PBKDF2_HMAC_FunctionType | None = None, aes_cipher: type[AESDecryptor] | None = None):
    pbkdf2_hmac_implement(pbkdf2_hmac) if pbkdf2_hmac else None
    aes_cipher_implement(aes_cipher) if aes_cipher else None
    algorithm_handler(0x0063)(zip_aes_wrapper) if not is_algorithm_handlier_available(0x0063) else None
