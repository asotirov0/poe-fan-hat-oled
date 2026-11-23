"""Classic public-domain 5x7 font, printable ASCII 0x20..0x7E, plus a degree sign.

Each glyph is 5 column bytes; bit 0 is the top row, which matches the SSD13xx page layout
(one page = 8 pixel rows, one byte per column). Rendered with a 1-column gap, so a cell is
6 px wide: 21 characters fit on the 128 px display.
"""

_GLYPHS = bytes.fromhex(
    "0000000000"  # space
    "00005f0000"  # !
    "0007000700"  # "
    "147f147f14"  # #
    "242a7f2a12"  # $
    "2313086462"  # %
    "3649552250"  # &
    "0005030000"  # '
    "001c224100"  # (
    "0041221c00"  # )
    "082a1c2a08"  # *
    "08083e0808"  # +
    "0050300000"  # ,
    "0808080808"  # -
    "0060600000"  # .
    "2010080402"  # /
    "3e5149453e"  # 0
    "00427f4000"  # 1
    "4261514946"  # 2
    "2141454b31"  # 3
    "1814127f10"  # 4
    "2745454539"  # 5
    "3c4a494930"  # 6
    "0171090503"  # 7
    "3649494936"  # 8
    "064949291e"  # 9
    "0036360000"  # :
    "0056360000"  # ;
    "0814224100"  # <
    "1414141414"  # =
    "0041221408"  # >
    "0201510906"  # ?
    "324979413e"  # @
    "7e1111117e"  # A
    "7f49494936"  # B
    "3e41414122"  # C
    "7f4141221c"  # D
    "7f49494941"  # E
    "7f09090101"  # F
    "3e41415132"  # G
    "7f0808087f"  # H
    "00417f4100"  # I
    "2040413f01"  # J
    "7f08142241"  # K
    "7f40404040"  # L
    "7f0204027f"  # M
    "7f0408107f"  # N
    "3e4141413e"  # O
    "7f09090906"  # P
    "3e4151215e"  # Q
    "7f09192946"  # R
    "4649494931"  # S
    "01017f0101"  # T
    "3f4040403f"  # U
    "1f2040201f"  # V
    "7f2018207f"  # W
    "6314081463"  # X
    "0304780403"  # Y
    "6151494543"  # Z
    "00007f4141"  # [
    "0204081020"  # backslash
    "41417f0000"  # ]
    "0402010204"  # ^
    "4040404040"  # _
    "0001020400"  # `
    "2054545478"  # a
    "7f48444438"  # b
    "3844444420"  # c
    "384444487f"  # d
    "3854545418"  # e
    "087e090102"  # f
    "081454543c"  # g
    "7f08040478"  # h
    "00447d4000"  # i
    "2040443d00"  # j
    "007f102844"  # k
    "00417f4000"  # l
    "7c04180478"  # m
    "7c08040478"  # n
    "3844444438"  # o
    "7c14141408"  # p
    "081414187c"  # q
    "7c08040408"  # r
    "4854545420"  # s
    "043f444020"  # t
    "3c4040207c"  # u
    "1c2040201c"  # v
    "3c4030403c"  # w
    "4428102844"  # x
    "0c5050503c"  # y
    "4464544c44"  # z
    "0008364100"  # {
    "00007f0000"  # |
    "0041360800"  # }
    "0201020402"  # ~
)

FIRST = 0x20
LAST = 0x7E
CELL = 6  # 5 glyph columns + 1 gap
assert len(_GLYPHS) == (LAST - FIRST + 1) * 5

# The few characters used outside ASCII.
_EXTRA = {"\u00b0": bytes.fromhex("0006090906")}  # degree sign, drawn next to the unit


def glyph(ch: str) -> bytes:
    """Five column bytes for one character; anything unprintable renders as '?'."""
    if ch in _EXTRA:
        return _EXTRA[ch]
    code = ord(ch)
    if not FIRST <= code <= LAST:
        code = ord("?")
    i = (code - FIRST) * 5
    return _GLYPHS[i:i + 5]


def render_line(text: str, width: int = 128, x: int = 0) -> bytes:
    """One 8-pixel-high page: `text` drawn from column `x`, clipped to `width`."""
    buf = bytearray(width)
    for ch in text:
        if x >= width:
            break
        for col in glyph(ch):
            if 0 <= x < width:
                buf[x] = col
            x += 1
        x += CELL - 5
    return bytes(buf)


# Each byte with its 8 rows doubled to 16: bit i becomes bits 2i and 2i+1.
_TALL = [sum(3 << (2 * i) for i in range(8) if b >> i & 1) for b in range(256)]


def stretch(page: bytes) -> tuple[bytes, bytes]:
    """Double one 8-row page vertically into two pages (top, bottom)."""
    return bytes(_TALL[b] & 0xFF for b in page), bytes(_TALL[b] >> 8 for b in page)
