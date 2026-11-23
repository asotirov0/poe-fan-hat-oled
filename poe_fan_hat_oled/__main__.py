"""Daemon entry point.

Settings come from the environment (systemd loads /etc/default/poe-fan-hat-oled):
  OLED_BUS=1  OLED_ADDR=0x3c  OLED_IFACE=  OLED_CONTRAST=64  OLED_INTERVAL=2
  OLED_SHIFT_MINUTES=5  OLED_FLIP=0  OLED_PAGE_SECONDS=4
  OLED_TEMP_UNIT=C  OLED_FLAG_HOLD=60  OLED_POWER=1

The four status lines are shown two at a time in a double-height font, alternating every
OLED_PAGE_SECONDS. With OLED_PAGE_SECONDS=0 all four are shown at once in the small font.

A missing bus or panel is not fatal: the daemon logs once and retries, so it never
crash-loops on a host without the HAT. On SIGTERM it blanks the panel.
"""

import argparse
import os
import signal
import sys
import time

from . import __version__, screen, stats
from .ssd1316 import Display

RETRY_SECONDS = 30
SHIFT_STEPS = (0, 1, 2, 1)  # burn-in protection: move the text by up to 2 px


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def env_int(name: str, default: int, lo: int, hi: int, show=str) -> int:
    """Integer setting; anything unparsable or outside lo..hi falls back to the default."""
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw, 0)
    except ValueError:
        log(f"ignoring {name}={raw!r}: not a number, using {show(default)}")
        return default
    if not lo <= value <= hi:
        log(f"ignoring {name}={raw!r}: outside {show(lo)}..{show(hi)}, "
            f"using {show(default)}")
        return default
    return value


def temp_unit() -> str:
    raw = os.environ.get("OLED_TEMP_UNIT", "").strip().upper()
    if raw in ("", "C", "CELSIUS"):
        return "C"
    if raw in ("F", "FAHRENHEIT"):
        return "F"
    log(f"ignoring OLED_TEMP_UNIT={raw!r}: expected C or F, using C")
    return "C"


class Stop(Exception):
    pass


def run() -> int:
    bus = env_int("OLED_BUS", 1, 0, 255)
    # Valid 7-bit addresses without the reserved ones.
    addr = env_int("OLED_ADDR", 0x3C, 0x08, 0x77, show=hex)
    iface = os.environ.get("OLED_IFACE", "").strip() or None
    interval = env_int("OLED_INTERVAL", 2, 1, 3600)
    shift_s = env_int("OLED_SHIFT_MINUTES", 5, 0, 1440) * 60
    page_s = env_int("OLED_PAGE_SECONDS", 4, 0, 3600)
    unit = temp_unit()
    hold = env_int("OLED_FLAG_HOLD", 60, 0, 86400)
    latch = screen.Latch(hold) if hold else None
    show_power = env_int("OLED_POWER", 1, 0, 1) == 1
    smooth = screen.Smooth()
    tick = min(interval, page_s) if page_s else interval
    disp = Display(bus, addr, env_int("OLED_CONTRAST", 64, 0, 255),
                   env_int("OLED_FLIP", 0, 0, 1) == 1)

    def _stop(signum, _frame):
        raise Stop()

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    log(f"poe-fan-hat-oled {__version__}: {disp.path} addr 0x{addr:02x}")
    source = stats.throttled_raw()
    log(f"throttle flags from {source[1] if source else 'nowhere (not readable)'}")
    if show_power:
        log("board power estimate " + ("from the PMIC" if stats.power() is not None
                                       else "not available on this hardware"))

    last_error = None
    try:
        while True:
            try:
                disp.open()
                disp.init()
                if last_error:
                    log("display found, running")
                last_error = None
                shown = None
                while True:
                    now = time.monotonic()
                    step = int(now // shift_s) % len(SHIFT_STEPS) if shift_s else 0
                    watts = smooth.update(stats.power()) if show_power else None
                    text = screen.lines(iface, unit=unit, latch=latch, now=now, watts=watts)
                    if page_s:  # two lines per page, pages alternate
                        first = int(now // page_s) % (len(text) // 2) * 2
                        text = text[first:first + 2]
                    fr = screen.frame(text, SHIFT_STEPS[step], tall=bool(page_s))
                    if fr != shown:
                        disp.show(fr)
                        shown = fr
                    time.sleep(tick)
            except OSError as e:
                msg = f"{type(e).__name__}: {e}"
                if msg != last_error:
                    log(f"display unavailable ({msg}); retrying every {RETRY_SECONDS}s")
                    last_error = msg
                disp.close()
                time.sleep(RETRY_SECONDS)
    except Stop:
        # A second signal must not interrupt the blanking below.
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        if disp.fd is not None:
            try:
                disp.off()
            except OSError:
                pass
        disp.close()
        log("stopped, display blanked")
        return 0


def main() -> int:
    p = argparse.ArgumentParser(prog="poe-fan-hat-oled", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--print", action="store_true",
                   help="print the four status lines once and exit (no display access)")
    p.add_argument("--version", action="version", version=__version__)
    a = p.parse_args()
    if a.print:
        iface = os.environ.get("OLED_IFACE", "").strip() or None
        watts = stats.power() if env_int("OLED_POWER", 1, 0, 1) == 1 else None
        print("\n".join(screen.lines(iface, unit=temp_unit(), watts=watts)))
        return 0
    return run()


if __name__ == "__main__":
    sys.exit(main())
