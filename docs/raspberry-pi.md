# Run Brand Intel on a Raspberry Pi

This guide turns a Raspberry Pi 5 into an always-on brand monitor. The dashboard runs around the clock,
scans your brand every morning, and emails you when something needs attention. It takes about 30 minutes,
most of it waiting for downloads.

Written for the **CanaKit Raspberry Pi 5 Starter Kit (8GB, 128GB)**. It works on any Pi 4 or 5 running
64-bit Raspberry Pi OS.

## 1. Start the Pi

**Easiest: use a monitor and keyboard.**
1. Plug the microSD card from the kit into the Pi.
2. Connect a monitor (the kit includes micro-HDMI cables), a keyboard, a mouse, and ethernet if you have it.
3. Plug in the power supply last.
4. CanaKit cards normally come with Raspberry Pi OS already on them. Follow the welcome screens to set your
   country, a username and password, and Wi-Fi.
5. When the desktop appears, open **Terminal** (the black icon at the top). You'll type the commands below
   there.

**If your card is blank, or you want to skip the monitor:**
1. On your computer, install [Raspberry Pi Imager](https://www.raspberrypi.com/software/).
2. Choose **Raspberry Pi 5** and **Raspberry Pi OS (64-bit)**.
3. In the settings (gear icon), set a hostname such as `brandintel`, your username and password, and your
   Wi-Fi, and turn on **SSH**.
4. Write the card and boot the Pi.
5. From your computer, connect with `ssh yourname@brandintel.local`.

## 2. Install Brand Intel

Paste these two lines into the terminal:

```bash
git clone https://github.com/Sweitzerc/brand-intel.git
cd brand-intel && bash pi/install.sh
```

The installer:
- installs Python
- downloads the AI models (5–10 minutes the first time)
- sets the dashboard to start whenever the Pi turns on
- schedules a scan every morning at 7:00

When it finishes, it prints the dashboard's address, something like `http://brandintel.local:7860`. Open
that address on any phone or computer on the same Wi-Fi.

## 3. Add your settings

Open the settings file:

```bash
nano ~/brand-intel/.env
```

Fill in:

- **`BRAND_INTEL_PASSWORD`**: a password for the dashboard. The username is `admin`. Set this so other
  people on your Wi-Fi can't open it.
- **`SMTP_USER`**: your Gmail address.
- **`SMTP_PASSWORD`**: a Gmail **App Password**, not your normal password. Create one at
  https://myaccount.google.com/apppasswords. Google requires 2-Step Verification to be on first.
- **`ALERT_TO`**: where alerts should go. This can be the same address.

Save with **Ctrl+O**, then **Enter**, then exit with **Ctrl+X**. Restart the dashboard:

```bash
sudo systemctl restart brand-intel
```

Next, edit `~/brand-intel/config.yaml` the same way to set your competitors, domains, topics, alert levels,
and `contact_email` (the SEC search needs it).

## 4. Check that alerts work

```bash
cd ~/brand-intel && .venv/bin/python -m brand_intel.monitor --dry-run
```

This runs one scan and prints what it would email, without sending anything.

The first run records a baseline, so it doesn't flood you with alerts. From the next morning on, you're
emailed only about:
- **new** negative mentions
- **newly** registered lookalike domains
- overall tone dropping below the level you set

## Everyday commands

| To… | Run |
|---|---|
| Get the latest code | `cd ~/brand-intel && bash pi/install.sh` |
| Restart the dashboard | `sudo systemctl restart brand-intel` |
| See whether it's running | `systemctl status brand-intel` |
| Read its log | `journalctl -u brand-intel -n 50` |
| Run the morning scan now | `sudo systemctl start brand-intel-monitor` |
| Change the scan time | `sudo systemctl edit --full brand-intel-monitor.timer` |

## Open it away from home (optional)

Install [Tailscale](https://tailscale.com/download) on the Pi and on your phone, and sign in to both with
the same account. Your phone can then reach the dashboard from anywhere. Use the Pi's Tailscale name, for
example `http://brandintel:7860`. Nothing is exposed to the public internet.

To install it on the Pi:

```bash
curl -fsSL https://tailscale.com/install.sh | sh && sudo tailscale up
```

## Using the second Pi

One Pi runs Brand Intel comfortably. Some good uses for the second:

- **Backup.** Install Brand Intel on it too, and copy `data/brand_intel.db` over every week. A failed SD
  card then costs you nothing.
- **A separate brand.** Run a second copy with its own `config.yaml`, for example for another Galore
  Brands store.
- **Archiving.** Bellingcat's full [auto-archiver](https://github.com/bellingcat/auto-archiver) is heavier
  than the Evidence Locker tab. Running it on the second Pi keeps the dashboard fast.
