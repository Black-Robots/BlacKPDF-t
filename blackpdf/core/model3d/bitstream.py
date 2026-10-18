"""Lector de bits con decodificación aritmética de U3D (ECMA-363, anexo A).

Cada bloque U3D se lee con su propio lector. Los valores "sin comprimir"
también pasan por el decodificador aritmético con un contexto estático de
256 símbolos, así que todo el bloque debe leerse en orden con esta clase.
"""

from __future__ import annotations

import struct

STATIC_FULL = 0x400
MAX_RANGE = STATIC_FULL + 0x3FFF
ELEPHANT = 0x1FFF
MAX_SYMBOL = 0xFFFF


def _reverse_table(bits: int) -> list[int]:
    table = []
    for value in range(1 << bits):
        result = 0
        for _ in range(bits):
            result = (result << 1) | (value & 1)
            value >>= 1
        table.append(result)
    return table


_REV15 = _reverse_table(15)
_REV8 = _reverse_table(8)


class BitStreamError(Exception):
    """Los datos se acaban antes de tiempo o no son coherentes."""


class _Histogram:
    """Histograma adaptativo de un contexto dinámico (árbol de Fenwick)."""

    __slots__ = ("counts", "tree", "size", "total")

    def __init__(self):
        self.size = 64
        self.counts = [0] * self.size
        self.tree = [0] * (self.size + 1)
        self.total = 0
        self._add(0, 1)

    def _add(self, symbol: int, amount: int) -> None:
        self.counts[symbol] += amount
        self.total += amount
        i = symbol + 1
        tree, size = self.tree, self.size
        while i <= size:
            tree[i] += amount
            i += i & -i

    def _rebuild(self) -> None:
        size = self.size
        tree = [0] + self.counts
        for index in range(1, size + 1):
            parent = index + (index & -index)
            if parent <= size:
                tree[parent] += tree[index]
        self.tree = tree
        self.total = sum(self.counts)

    def _grow(self, symbol: int) -> None:
        size = self.size
        while size <= symbol:
            size *= 2
        self.counts.extend([0] * (size - self.size))
        self.size = size
        self._rebuild()

    def cumulative(self, symbol: int) -> int:
        """Suma de frecuencias de los símbolos menores que ``symbol``."""
        if symbol > self.size:
            return self.total
        result = 0
        tree = self.tree
        i = symbol
        while i > 0:
            result += tree[i]
            i -= i & -i
        return result

    def frequency(self, symbol: int) -> int:
        return self.counts[symbol] if symbol < self.size else 0

    def symbol_from(self, frequency: int) -> int:
        """Mayor símbolo cuya frecuencia acumulada no supera ``frequency``."""
        position = 0
        tree, size = self.tree, self.size
        step = 1 << (size.bit_length() - 1)
        while step:
            nxt = position + step
            if nxt <= size and tree[nxt] <= frequency:
                position = nxt
                frequency -= tree[nxt]
            step >>= 1
        return position

    def add_symbol(self, symbol: int) -> None:
        if symbol > MAX_SYMBOL:
            return
        if self.total >= ELEPHANT:
            self.counts = [count >> 1 for count in self.counts]
            self.counts[0] += 1
            self._rebuild()
        if symbol >= self.size:
            self._grow(symbol)
        self._add(symbol, 1)


class BitReader:
    """Lee valores de un bloque U3D."""

    def __init__(self, data: bytes, no_compression: bool = False):
        self.data = bytes(data) + b"\0" * 8
        self.limit = len(data) * 8
        self.position = 0
        self.low = 0
        self.high = 0xFFFF
        self.underflow = 0
        self.no_compression = no_compression
        self.contexts: dict[int, _Histogram] = {}

    # -- bits sueltos ---------------------------------------------------

    def _bits(self, position: int, count: int) -> int:
        index = position >> 3
        word = int.from_bytes(self.data[index:index + 4], "little")
        return (word >> (position & 7)) & ((1 << count) - 1)

    def _code(self) -> int:
        position = self.position
        if position > self.limit + 64:
            raise BitStreamError("fin de datos")
        first = self._bits(position, 1)
        rest = self._bits(position + 1 + self.underflow, 15)
        return (first << 15) | _REV15[rest]

    # -- decodificación aritmética ---------------------------------------

    def _normalize(self, low: int, high: int) -> None:
        count = 0
        while (low & 0x8000) == (high & 0x8000):
            low = (low & 0x7FFF) << 1
            high = ((high & 0x7FFF) << 1) | 1
            count += 1
        if count:
            count += self.underflow
            self.underflow = 0
        while (low & 0x4000) and not (high & 0x4000):
            low = (low & 0x3FFF) << 1
            high = ((high & 0x3FFF) << 1) | 1
            self.underflow += 1
        self.low = low
        self.high = high | 0x8000
        self.position += count

    def _static_symbol(self, count: int) -> int:
        """Devuelve un símbolo 1..count de un contexto estático uniforme."""
        code = self._code()
        low = self.low
        span = self.high + 1 - low
        cumulative = (count * (1 + code - low) - 1) // span
        if cumulative >= count:
            raise BitStreamError("símbolo fuera de rango")
        high = low - 1 + span * (cumulative + 1) // count
        low = low + span * cumulative // count
        self._normalize(low, high)
        return cumulative + 1

    def _dynamic_symbol(self, context: int) -> int:
        histogram = self.contexts.get(context)
        if histogram is None:
            histogram = self.contexts[context] = _Histogram()
        code = self._code()
        low = self.low
        span = self.high + 1 - low
        total = histogram.total
        cumulative = (total * (1 + code - low) - 1) // span
        symbol = histogram.symbol_from(cumulative)
        start = histogram.cumulative(symbol)
        frequency = histogram.frequency(symbol)
        if frequency == 0:
            raise BitStreamError("símbolo desconocido")
        high = low - 1 + span * (start + frequency) // total
        low = low + span * start // total
        histogram.add_symbol(symbol)
        self._normalize(low, high)
        return symbol

    def _symbol(self, context: int) -> int:
        if context > STATIC_FULL:
            return self._static_symbol(context - STATIC_FULL)
        return self._dynamic_symbol(context)

    # -- valores sin comprimir --------------------------------------------

    def u8(self) -> int:
        if self.high == 0xFFFF and self.low == 0 and self.underflow == 0:
            if self.position + 8 > self.limit + 64:
                raise BitStreamError("fin de datos")
            value = self._bits(self.position, 8)
            self.position += 8
            return value
        return _REV8[self._static_symbol(256) - 1]

    def u16(self) -> int:
        low = self.u8()
        return low | (self.u8() << 8)

    def u32(self) -> int:
        low = self.u16()
        return low | (self.u16() << 16)

    def u64(self) -> int:
        low = self.u32()
        return low | (self.u32() << 32)

    def i32(self) -> int:
        value = self.u32()
        return value - (1 << 32) if value & 0x80000000 else value

    def i16(self) -> int:
        value = self.u16()
        return value - (1 << 16) if value & 0x8000 else value

    def f32(self) -> float:
        return struct.unpack("<f", struct.pack("<I", self.u32()))[0]

    def f64(self) -> float:
        return struct.unpack("<d", struct.pack("<Q", self.u64()))[0]

    def f32s(self, count: int) -> list[float]:
        return [self.f32() for _ in range(count)]

    def string(self) -> str:
        length = self.u16()
        raw = bytes(self.u8() for _ in range(length))
        return raw.decode("utf-8", errors="replace")

    def bytes_left(self) -> bytes:
        """Resto del bloque como bytes (solo válido con el decodificador en reposo)."""
        start = (self.position + 7) // 8
        return self.data[start:self.limit // 8]

    @property
    def byte_position(self) -> int:
        return (self.position + 7) // 8

    # -- valores comprimidos ------------------------------------------------

    def _compressed(self, context: int, raw) -> int:
        if self.no_compression or not context or context >= MAX_RANGE:
            return raw()
        symbol = self._symbol(context)
        if symbol:
            return symbol - 1
        value = raw()
        if context <= STATIC_FULL:
            histogram = self.contexts.get(context)
            if histogram is None:
                histogram = self.contexts[context] = _Histogram()
            histogram.add_symbol(value + 1)
        return value

    def cu32(self, context: int) -> int:
        return self._compressed(context, self.u32)

    def cu16(self, context: int) -> int:
        return self._compressed(context, self.u16)

    def cu8(self, context: int) -> int:
        return self._compressed(context, self.u8)
