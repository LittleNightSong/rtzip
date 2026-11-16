class RtZipError(Exception):
    ...


class UnsupportedAlgorithmError(RtZipError):
    ...


class WrongPasswordError(RtZipError):
    ...


class BadZipError(RtZipError):
    ...


class WinzipAESError(RtZipError):
    ...


class BadWinzipAESData(WinzipAESError):
    ...


class HMACError(WinzipAESError):
    ...


class NotEnoughDataError(BadZipError):
    def __init__(self, require, available):
        super().__init__(require, available)
        self.require = require
        self.available = available

    def __str__(self):
        return f"require {self.require} byte(s) but only {self.available} available"


class InvalidOffsetError(BadZipError):
    def __init__(self, start, length, available):
        super().__init__(start, length, available)
        self.start = start
        self.length = length
        self.available = available

    def __str__(self):
        return (
            f"invalid offset {self.start} + {self.length} "
            f"(available: {self.available})"
        )
