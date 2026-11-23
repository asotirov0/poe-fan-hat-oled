"""Host readings for the display, from /proc, /sys and the Pi firmware mailbox (no
subprocesses, no network).

Every reader takes a `root` prefix so the tests can point it at a fake tree.
"""

import fcntl
import glob
import os
import socket
import struct

SIOCGIFADDR = 0x8915

# get_throttled bits that describe the CURRENT state (bits 16-19 are "has occurred").
THROTTLE_NOW = {0: "UV", 1: "CAP", 2: "THR", 3: "HOT"}

# Firmware property mailbox, the interface vcgencmd uses (linux/drivers/char/broadcom/vcio.c).
VCIO = "/dev/vcio"
IOCTL_MBOX_PROPERTY = (3 << 30) | (struct.calcsize("P") << 16) | (100 << 8)  # _IOWR(100, 0, char *)
TAG_GET_THROTTLED = 0x00030046
MBOX_OK = 0x80000000
# The kernel exposes the same value here, but reading it logs a deprecation WARNING.
THROTTLED_SYSFS = "/sys/devices/platform/soc/soc:firmware/get_throttled"


def _read(path: str) -> str | None:
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return None


def hostname() -> str:
    return socket.gethostname().split(".")[0]


def uptime(root: str = "") -> str:
    raw = _read(f"{root}/proc/uptime")
    if not raw:
        return "?"
    s = int(float(raw.split()[0]))
    d, s = divmod(s, 86400)
    h, s = divmod(s, 3600)
    m = s // 60
    return f"{d}d{h:02d}h" if d else f"{h}h{m:02d}m"


def default_iface(root: str = "") -> str | None:
    """Interface of the default route with the lowest metric."""
    raw = _read(f"{root}/proc/net/route")
    if not raw:
        return None
    best: tuple[int, str] | None = None
    for line in raw.splitlines()[1:]:
        f = line.split()
        if len(f) >= 8 and f[1] == "00000000" and f[7] == "00000000" and f[6].isdigit():
            metric = int(f[6])
            if best is None or metric < best[0]:
                best = (metric, f[0])
    return best[1] if best else None


def ipv4(iface: str) -> str | None:
    """IPv4 of `iface` via an ioctl. The socket is never connected, so nothing is sent."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            res = fcntl.ioctl(s.fileno(), SIOCGIFADDR, struct.pack("256s", iface[:15].encode()))
        except OSError:
            return None
    return socket.inet_ntoa(res[20:24])


def cpu_temp(root: str = "") -> float | None:
    raw = _read(f"{root}/sys/class/thermal/thermal_zone0/temp")
    return int(raw) / 1000 if raw and raw.lstrip("-").isdigit() else None


def fan(root: str = "") -> tuple[int, int] | None:
    """(rpm, pwm 0-255) from the pwm-fan hwmon, or None when no fan is bound."""
    for h in glob.glob(f"{root}/sys/class/hwmon/hwmon*"):
        if _read(os.path.join(h, "name")) == "pwmfan":
            rpm = _read(os.path.join(h, "fan1_input"))
            pwm = _read(os.path.join(h, "pwm1"))
            return (int(rpm) if rpm and rpm.isdigit() else 0,
                    int(pwm) if pwm and pwm.isdigit() else 0)
    return None


def mbox_request(tag: int, value: int = 0) -> bytearray:
    """One-tag property message with a 4-byte value: size, request code, tag, value size,
    request length, value, end tag."""
    return bytearray(struct.pack("=7I", 28, 0, tag, 4, 0, value, 0))


def mbox_value(buf: bytes) -> int | None:
    """The value from a firmware reply, or None when the firmware did not answer the tag."""
    _size, code, _tag, _vsize, length, value, _end = struct.unpack("=7I", buf[:28])
    return value if code == MBOX_OK and length & MBOX_OK else None


def _throttled_vcio(root: str = "") -> int | None:
    try:
        fd = os.open(f"{root}{VCIO}", os.O_RDONLY)
    except OSError:
        return None
    try:
        # Value 0 = clear no sticky bits; this only reads.
        buf = mbox_request(TAG_GET_THROTTLED, 0)
        fcntl.ioctl(fd, IOCTL_MBOX_PROPERTY, buf, True)
        return mbox_value(buf)
    except OSError:
        return None
    finally:
        os.close(fd)


def throttled_raw(root: str = "") -> tuple[int, str] | None:
    """(get_throttled value, source) from the firmware mailbox, else the sysfs node."""
    v = _throttled_vcio(root)
    if v is not None:
        return v, VCIO
    raw = _read(f"{root}{THROTTLED_SYSFS}")
    if raw is None:
        return None
    try:
        return int(raw, 16), THROTTLED_SYSFS
    except ValueError:
        return None


def throttled(root: str = "") -> list[str] | None:
    """Names of the throttle conditions active right now; None when unreadable."""
    got = throttled_raw(root)
    if got is None:
        return None
    return [name for bit, name in THROTTLE_NOW.items() if got[0] & (1 << bit)]


def load1(root: str = "") -> float | None:
    raw = _read(f"{root}/proc/loadavg")
    return float(raw.split()[0]) if raw else None
