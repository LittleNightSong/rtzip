class UnsupportedAlgorithmError(Exception):
    ...


class UnsupportedCryptoError(Exception):
    ...


class UnsupportedDataDescriptorError(Exception):
    ...


class WrongPasswordError(Exception):
    ...


class BadZipError(Exception):
    ...


class BadWinzipAESData(BadZipError):
    ...


class HMACError(Exception):
    ...

