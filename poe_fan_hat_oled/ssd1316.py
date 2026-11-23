"""Minimal SSD1316 (128x32) driver over Linux i2c-dev, standard library only.

Every I2C write starts with a control byte: 0x00 = command stream, 0x40 = display data.
The init sequence follows the Seengreat reference code for this panel.
"""

import fcntl
import os

I2C_SLAVE = 0x0703  # linux/i2c-dev.h
WIDTH = 128
PAGES = 4  # 32 rows / 8
FRAME_BYTES = WIDTH * PAGES


class Display:
    def __init__(self, bus: int = 1, addr: int = 0x3C, contrast: int = 0x40, flip: bool = False):
        self.path = f"/dev/i2c-{bus}"
        self.addr = addr
        self.contrast = max(0, min(255, contrast))
        self.flip = flip
        self.fd: int | None = None

    def open(self) -> None:
        fd = os.open(self.path, os.O_RDWR)
        try:
            fcntl.ioctl(fd, I2C_SLAVE, self.addr)
        except OSError:
            os.close(fd)
            raise
        self.fd = fd

    def close(self) -> None:
        if self.fd is not None:
            try:
                os.close(self.fd)
            finally:
                self.fd = None

    def _write(self, data: bytes) -> None:
        assert self.fd is not None, "display not open"
        os.write(self.fd, data)

    def command(self, *cmds: int) -> None:
        self._write(bytes((0x00, *cmds)))

    def init(self) -> None:
        # Segment remap / COM scan direction pick the orientation; flip = rotate 180 degrees.
        seg, com = (0xA0, 0xC8) if self.flip else (0xA1, 0xC0)
        self.command(
            0xAE,                    # display off
            0x00, 0x10,              # column address 0
            0x40,                    # start line 0
            0xB0,                    # page 0
            0x81, self.contrast,     # contrast
            seg,                     # segment remap
            0xA6,                    # normal (not inverted)
            0xA8, 0x1F,              # multiplex 1/32
            com,                     # COM scan direction
            0xD3, 0x00,              # display offset 0
            0xD5, 0x80,              # oscillator / clock divide
            0xD9, 0x22,              # pre-charge period
            0xDA, 0x12,              # COM pins configuration
            0xDB, 0x20,              # VCOMH level
            0x8D, 0x14,              # charge pump on
        )
        self.show(bytes(FRAME_BYTES))
        self.command(0xAF)           # display on

    def show(self, frame: bytes) -> None:
        if len(frame) != FRAME_BYTES:
            raise ValueError(f"frame must be {FRAME_BYTES} bytes, got {len(frame)}")
        for page in range(PAGES):
            self.command(0xB0 + page, 0x00, 0x10)
            self._write(b"\x40" + frame[page * WIDTH:(page + 1) * WIDTH])

    def off(self) -> None:
        """Blank the panel and stop the charge pump (used on service stop)."""
        self.command(0xAE, 0x8D, 0x10)
