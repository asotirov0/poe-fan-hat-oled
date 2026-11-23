# poe-fan-hat-oled

Status display for the 0.87" 128×32 OLED (SSD1316, I2C bus 1, address `0x3c`) on the
**Seengreat PoE Fan HAT (C)** for the Raspberry Pi 5.

The panel has only 32 pixel rows, so the four status lines are shown two at a time in a
double-height font. The display alternates between two pages every 4 seconds:

```text
page 1                     page 2
+---------------------+    +---------------------+
|node1      up   9d06h|    |56.8°C fan 3043      |
|eth0 192.168.1.51    |    |load 0.74 OK         |
+---------------------+    +---------------------+
```

| Page | Line | Shows |
| --- | --- | --- |
| 1 | 1 | short hostname, uptime |
| 1 | 2 | interface of the default route (or `OLED_IFACE`) and its IPv4; the address alone if both do not fit |
| 2 | 3 | CPU temperature in °C or °F (`OLED_TEMP_UNIT`); fan rpm, `fan 0rpm!` (driven but no tach pulses), `fan off`, or `fan --` (no fan bound) |
| 2 | 4 | 1-minute load; `OK` or the throttle flags seen in the last 60 seconds (`OLED_FLAG_HOLD`): `UV` under-voltage, `CAP` frequency capped, `THR` throttled, `HOT` soft temperature limit |

`OLED_PAGE_SECONDS` sets how long each page stays up. With `OLED_PAGE_SECONDS=0` there is no
paging: all four lines are shown at once in the small 5x7 font.

The HAT's fan is not driven by this package. It plugs into the Pi 5 FAN header and is run by
the kernel's `pwm_fan` driver.

## Requirements

- Raspberry Pi 5 with the HAT fitted. The package targets Ubuntu 24.04 (noble); any
  systemd distribution with Python 3.10 or newer should work.
- I2C enabled and `/dev/i2c-1` present. Ubuntu's Pi images enable it by default. On
  Raspberry Pi OS run `sudo raspi-config nonint do_i2c 0`, or do it by hand:

  ```bash
  echo 'dtparam=i2c_arm=on' | sudo tee -a /boot/firmware/config.txt
  echo i2c-dev | sudo tee /etc/modules-load.d/i2c-dev.conf
  sudo reboot
  ```

Without the bus the service keeps running and logs `display unavailable` to the journal.

## Build

Tagged versions have a prebuilt `.deb` on the GitHub Releases page, built by the workflow in
`.github/workflows/build.yml`. To build it yourself on any Debian or Ubuntu machine:

```bash
sudo apt install build-essential debhelper python3
python3 -m unittest discover -s tests -v
dpkg-buildpackage -us -uc -b      # tests run again during the build
```

The package lands in the parent directory.

## Install

```bash
sudo apt install ../poe-fan-hat-oled_<version>_all.deb
poe-fan-hat-oled --print          # all four lines at once, without touching the display
systemctl status poe-fan-hat-oled
```

Settings: `/etc/default/poe-fan-hat-oled` (bus, address, interface, temperature unit, flag hold
time, brightness, refresh, page time, burn-in shift, 180° flip). Restart the service after editing.

## Design

- Standard library only: `i2c-dev` via `ioctl`, readings from `/proc` and `/sys`. No numpy,
  smbus or lgpio, and no `vcgencmd`.
- Throttle flags come from the firmware mailbox (`/dev/vcio`, the interface `vcgencmd` uses).
  The kernel's `get_throttled` sysfs file holds the same value, but it is deprecated and
  reading it logs a kernel warning, so it is only the fallback when `/dev/vcio` cannot be
  opened. The service log says which source is in use.
- The flags change within seconds near a limit, so each one stays on screen for a minute
  after it was last seen.
- No network traffic: the unit sets `IPAddressDeny=any`. The vendor demo found its IP by
  connecting a socket to 8.8.8.8; this reads it from the interface instead.
- Runs as a `DynamicUser` with only the i2c devices and `/dev/vcio` allowed
  (`DevicePolicy=closed`). `/dev/vcio` is opened read-only, but note that the mailbox can do
  more than report throttling; the daemon sends it one fixed query and nothing else.
- No HAT or no bus: logs once and retries every 30 s. It never crash-loops, so the package
  can be installed on hosts without the hardware.
- On stop the panel is blanked and its charge pump switched off.
- Burn-in: low default contrast and a 0–2 px horizontal shift every 5 minutes.

## License

Apache-2.0, see [LICENSE](LICENSE). The 5x7 ASCII font table is the generic character-LCD font
of unknown origin, treated as public domain; see [debian/copyright](debian/copyright).
