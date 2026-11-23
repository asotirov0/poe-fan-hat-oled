"""Four 21-character status lines -> 128x32 frames (all four small, or two double-height)."""

from . import stats
from .font import render_line, stretch
from .ssd1316 import PAGES, WIDTH

COLUMNS = 21  # characters per line: 128 px / 6 px cell


class Latch:
    """Keeps a throttle flag on screen for `hold` seconds after it was last seen.

    The flags flip within seconds near a limit, so a single snapshot can miss them.
    """

    def __init__(self, hold: float):
        self.hold = hold
        self.seen: dict[str, float] = {}

    def update(self, flags: list[str], now: float) -> list[str]:
        for f in flags:
            self.seen[f] = now
        return [f for f in stats.THROTTLE_NOW.values()
                if f in self.seen and now - self.seen[f] <= self.hold]


class Smooth:
    """Exponential moving average; the power reading jumps with every burst of CPU work."""

    def __init__(self, weight: float = 0.3):
        self.weight = weight
        self.value: float | None = None

    def update(self, sample: float | None) -> float | None:
        if sample is None:
            self.value = None
        elif self.value is None:
            self.value = sample
        else:
            self.value += self.weight * (sample - self.value)
        return self.value


def fan_text(f: tuple[int, int] | None) -> str:
    if f is None:
        return "fan --"          # no pwm-fan bound: firmware found no fan
    rpm, pwm = f
    if pwm == 0:
        return "fan off"
    if rpm == 0:
        return "fan 0rpm!"       # driven but no tach pulses: stalled fan or dead tach wire
    return f"fan {rpm}"


def lines(iface: str | None = None, root: str = "", unit: str = "C",
          latch: Latch | None = None, now: float = 0.0,
          watts: float | None = None) -> list[str]:
    ifc = iface or stats.default_iface(root) or "?"
    ip = stats.ipv4(ifc) if ifc != "?" else None
    t = stats.cpu_temp(root)
    if t is not None and unit == "F":
        t = t * 9 / 5 + 32
    load = stats.load1(root)
    thr = stats.throttled(root)
    if latch is not None and thr is not None:
        thr = latch.update(thr, now)

    l1 = f"{stats.hostname()[:10]:<10} up {stats.uptime(root):>7}"
    l2 = f"{ifc} {ip or 'no IPv4'}"
    if ip and len(l2) > COLUMNS:
        l2 = ip                  # long interface name: the address matters more
    l3 = f"{'--.-' if t is None else f'{t:4.1f}'}°{unit} {fan_text(stats.fan(root))}"
    if watts is not None and len(f"{l3} {watts:.1f}W") <= COLUMNS:
        l3 = f"{l3} {watts:.1f}W"   # left out when the line is already full
    status = "OK" if thr == [] else ("?" if thr is None else " ".join(thr))
    load_s = "-" if load is None else f"{load:.2f}"
    l4 = f"load {load_s} {status}"
    if len(l4) > COLUMNS:
        l4 = f"{load_s} {status}"   # all four flags at once: drop the label
    return [l1, l2, l3, l4]


def frame(text: list[str], x: int = 0, tall: bool = False) -> bytes:
    """Small: up to four 8-row lines. Tall: up to two lines at double height."""
    rows = PAGES // 2 if tall else PAGES
    out = bytearray()
    for i in range(rows):
        page = render_line(text[i] if i < len(text) else "", WIDTH, x)
        if tall:
            top, bottom = stretch(page)
            out += top + bottom
        else:
            out += page
    return bytes(out)
