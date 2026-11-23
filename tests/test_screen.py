import os
import tempfile
import unittest

from poe_fan_hat_oled import font, screen, stats
from poe_fan_hat_oled.ssd1316 import FRAME_BYTES


def write(root, rel, text):
    path = os.path.join(root, rel.lstrip("/"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)


ROUTE = (
    "Iface\tDestination\tGateway \tFlags\tRefCnt\tUse\tMetric\tMask\t\tMTU\tWindow\tIRTT\n"
    "eth1\t00000000\t0102A8C0\t0003\t0\t0\t100\t00000000\t0\t0\t0\n"
    "eth0\t00000000\t0101A8C0\t0003\t0\t0\t0\t00000000\t0\t0\t0\n"
    "eth0\t0001A8C0\t00000000\t0001\t0\t0\t0\t00FFFFFF\t0\t0\t0\n"
)


class FakeHost(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        r = self.root = self.tmp.name
        write(r, "/proc/uptime", "277200.55 1000.0\n")
        write(r, "/proc/loadavg", "0.63 0.70 0.77 1/500 1234\n")
        write(r, "/proc/net/route", ROUTE)
        write(r, "/sys/class/thermal/thermal_zone0/temp", "62300\n")
        write(r, "/sys/devices/platform/soc/soc:firmware/get_throttled", "e0008\n")
        write(r, "/sys/class/hwmon/hwmon0/name", "cpu_thermal\n")
        write(r, "/sys/class/hwmon/hwmon2/name", "pwmfan\n")
        write(r, "/sys/class/hwmon/hwmon2/fan1_input", "3120\n")
        write(r, "/sys/class/hwmon/hwmon2/pwm1", "125\n")

    def tearDown(self):
        self.tmp.cleanup()

    def test_readers(self):
        r = self.root
        self.assertEqual(stats.uptime(r), "3d05h")
        self.assertEqual(stats.default_iface(r), "eth0")  # lowest metric wins
        self.assertAlmostEqual(stats.cpu_temp(r), 62.3)
        self.assertEqual(stats.fan(r), (3120, 125))
        self.assertEqual(stats.throttled(r), ["HOT"])  # only current-state bits
        self.assertEqual(stats.load1(r), 0.63)

    def test_lines_fit_the_display(self):
        text = screen.lines("lo", self.root)
        self.assertEqual(len(text), 4)
        self.assertEqual(text[1], "lo 127.0.0.1")
        self.assertEqual(text[2], "62.3°C fan 3120")
        self.assertEqual(text[3], "load 0.63 HOT")
        self.assertEqual(screen.lines("lo", self.root, "F")[2], "144.1°F fan 3120")
        for line in text:
            self.assertLessEqual(len(line), 21, line)

    def test_long_interface_name_gives_way_to_the_address(self):
        real = stats.ipv4
        stats.ipv4 = lambda iface: "192.168.100.200"
        try:
            self.assertEqual(screen.lines("eth0", self.root)[1], "eth0 192.168.100.200")
            self.assertEqual(screen.lines("enx00e04c680001", self.root)[1], "192.168.100.200")
        finally:
            stats.ipv4 = real

    def test_missing_readings_do_not_crash(self):
        empty = tempfile.TemporaryDirectory()
        try:
            text = screen.lines("nonexistent0", empty.name)
            self.assertIn("no IPv4", text[1])
            self.assertEqual(text[2], "--.-°C fan --")
            self.assertEqual(text[3], "load - ?")
        finally:
            empty.cleanup()

    def test_throttle_sources(self):
        r = self.root
        self.assertEqual(stats.throttled_raw(r), (0xE0008, stats.THROTTLED_SYSFS))
        # A /dev/vcio that is not the firmware device is ignored, not fatal.
        write(r, "/dev/vcio", "")
        self.assertEqual(stats.throttled_raw(r), (0xE0008, stats.THROTTLED_SYSFS))
        # Mailbox message layout and reply parsing.
        req = stats.mbox_request(stats.TAG_GET_THROTTLED)
        self.assertEqual(req.hex(), "1c00000000000000460003000400000000000000" "0000000000000000")
        self.assertIsNone(stats.mbox_value(req))  # unanswered request
        reply = bytes.fromhex("1c00000000000080460003000400000004000080" "0500050000000000")
        self.assertEqual(stats.mbox_value(reply), 0x50005)
        self.assertIn(stats.IOCTL_MBOX_PROPERTY, (0xC0086400, 0xC0046400))

    def test_flags_are_latched(self):
        latch = screen.Latch(60)
        self.assertEqual(latch.update(["HOT"], 0), ["HOT"])
        self.assertEqual(latch.update([], 59), ["HOT"])           # still held
        self.assertEqual(latch.update(["UV"], 60), ["UV", "HOT"])  # fixed order
        self.assertEqual(latch.update([], 61), ["UV"])            # HOT expired
        self.assertEqual(latch.update([], 121), [])
        r = self.root
        self.assertEqual(screen.lines("lo", r, latch=latch, now=200)[3], "load 0.63 HOT")
        write(r, "/sys/devices/platform/soc/soc:firmware/get_throttled", "e0000\n")
        self.assertEqual(screen.lines("lo", r, latch=latch, now=230)[3], "load 0.63 HOT")
        self.assertEqual(screen.lines("lo", r, latch=latch, now=300)[3], "load 0.63 OK")
        self.assertEqual(screen.lines("lo", r)[3], "load 0.63 OK")  # no latch: current state

    def test_all_flags_still_fit(self):
        write(self.root, "/sys/devices/platform/soc/soc:firmware/get_throttled", "f\n")
        write(self.root, "/proc/loadavg", "12.34 1 1 1/1 1\n")
        line = screen.lines("lo", self.root)[3]
        self.assertEqual(line, "12.34 UV CAP THR HOT")
        self.assertLessEqual(len(line), screen.COLUMNS)

    def test_fan_text(self):
        self.assertEqual(screen.fan_text(None), "fan --")
        self.assertEqual(screen.fan_text((0, 0)), "fan off")
        self.assertEqual(screen.fan_text((0, 250)), "fan 0rpm!")
        self.assertEqual(screen.fan_text((4100, 250)), "fan 4100")


class Rendering(unittest.TestCase):
    def test_glyphs(self):
        self.assertEqual(font.glyph("A"), bytes.fromhex("7e1111117e"))
        self.assertEqual(font.glyph("\x01"), font.glyph("?"))
        self.assertEqual(font.glyph("°"), bytes.fromhex("0006090906"))

    def test_line_and_frame(self):
        line = font.render_line("AB", 128, 2)
        self.assertEqual(len(line), 128)
        self.assertEqual(line[2:7], font.glyph("A"))
        self.assertEqual(line[8:13], font.glyph("B"))
        self.assertEqual(len(font.render_line("x" * 40)), 128)  # clipped, not wrapped
        self.assertEqual(len(screen.frame(["a", "b"])), FRAME_BYTES)

    def test_tall_frame(self):
        self.assertEqual(font.stretch(b"\x01\x80\x7f"), (b"\x03\x00\xff", b"\x00\xc0\x3f"))
        fr = screen.frame(["A", "B", "ignored"], tall=True)
        self.assertEqual(len(fr), FRAME_BYTES)
        top, bottom = font.stretch(font.render_line("A"))
        self.assertEqual(fr[:128], top)       # line 1 fills pages 0-1
        self.assertEqual(fr[128:256], bottom)
        self.assertEqual(fr[256:384], font.stretch(font.render_line("B"))[0])


if __name__ == "__main__":
    unittest.main()
