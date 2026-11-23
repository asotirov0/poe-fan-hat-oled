"""Host readings for the display, from /proc, /sys and the Pi firmware mailbox (no
subprocesses, no network).

Every reader takes a `root` prefix so the tests can point it at a fake tree.
"""

import fcntl
import glob
import os
import re
import socket
import struct

SIOCGIFADDR = 0x8915

# get_throttled bits that describe the CURRENT state (bits 16-19 are "has occurred").
THROTTLE_NOW = {0: "UV", 1: "CAP", 2: "THR", 3: "HOT"}

# Firmware property mailbox, the interface vcgencmd uses (linux/drivers/char/broadcom/vcio.c).
VCIO = "/dev/vcio"
IOCTL_MBOX_PROPERTY = (3 << 30) | (struct.calcsize("P") << 16) | (100 << 8)  # _IOWR(100, 0, char *)
TAG_GET_THROTTLED = 0x00030046
TAG_GET_GENCMD_RESULT = 0x00030080
GENCMD_MAX = 1024  # longest command or reply, as in vcgencmd
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


def _mbox_call(buf: bytearray, root: str = "") -> bool:
    """Send one property message to the firmware; the reply overwrites `buf`."""
    try:
        fd = os.open(f"{root}{VCIO}", os.O_RDONLY)
    except OSError:
        return False
    try:
        fcntl.ioctl(fd, IOCTL_MBOX_PROPERTY, buf, True)
        return True
    except OSError:
        return False
    finally:
        os.close(fd)


def _throttled_vcio(root: str = "") -> int | None:
    # Value 0 = clear no sticky bits; this only reads.
    buf = mbox_request(TAG_GET_THROTTLED, 0)
    return mbox_value(buf) if _mbox_call(buf, root) else None


def gencmd_request(command: str) -> bytearray:
    """Message for a firmware text command (what `vcgencmd <command>` sends): six header
    words (size, request code, tag, buffer size, request length, error), the command in a
    1024-byte string buffer, end tag."""
    text = command.encode("ascii")
    if len(text) >= GENCMD_MAX:
        raise ValueError("command too long")
    size = 6 * 4 + GENCMD_MAX + 4
    buf = bytearray(size)
    struct.pack_into("=6I", buf, 0, size, 0, TAG_GET_GENCMD_RESULT, GENCMD_MAX, 0, 0)
    buf[24:24 + len(text)] = text
    return buf


def gencmd_reply(buf: bytes) -> str | None:
    """The reply text, or None when the firmware reported an error."""
    _size, code, _tag, _blen, _rlen, error = struct.unpack_from("=6I", buf, 0)
    if code != MBOX_OK or error != 0:
        return None
    return bytes(buf[24:24 + GENCMD_MAX]).split(b"\0", 1)[0].decode("ascii", "replace")


_PMIC_AMPS = re.compile(r"(\w+)_A current\(\d+\)=([0-9.]+)A")
_PMIC_VOLTS = re.compile(r"(\w+)_V volt\(\d+\)=([0-9.]+)V")


def pmic_power(text: str) -> float | None:
    """Watts from `pmic_read_adc` output: the sum of volts x amps over every rail the PMIC
    reports both for. This is what the Pi 5 board itself consumes downstream of the PMIC;
    it excludes USB devices, the fan, the HAT and conversion losses."""
    amps = {name: float(v) for name, v in _PMIC_AMPS.findall(text)}
    volts = {name: float(v) for name, v in _PMIC_VOLTS.findall(text)}
    rails = amps.keys() & volts.keys()
    return sum(amps[r] * volts[r] for r in rails) if rails else None


def power(root: str = "") -> float | None:
    """Estimated board power in watts (Pi 5 only), or None when not available."""
    buf = gencmd_request("pmic_read_adc")
    if not _mbox_call(buf, root):
        return None
    text = gencmd_reply(buf)
    return pmic_power(text) if text else None


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
