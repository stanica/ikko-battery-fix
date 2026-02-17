# IKKO Battery Fix

Magisk module that fixes two kernel bugs on IKKO devices with the ETA6965 charger IC (MediaTek MT6789):

1. **25% CPU usage** from `charger_thread` busy loop
2. **False "Charging" notification** when USB-C OTG accessories are plugged in

## The Problems

### CPU busy loop

Two functions in the charger driver stack run in a tight feedback loop ~30 times per second:

1. **`eta6965_dump_register`** (in `eta6965_charger`) — reads all I2C registers from the charger IC and logs them via `printk`. Nothing consumes this data. Debug code left in production.

2. **`eta6965_dump_register`** (in `eta6965_charger_sec`) — same function duplicated in the secondary charger driver.

The result: `charger_thread` burns ~25% of one CPU core continuously, even with the screen off and no charger connected.

### OTG false charging detection

When a USB-C OTG adapter (or any VBUS-providing accessory) is plugged in, the device supplies 5V on VBUS for the attached peripheral. The ETA6965 charger IC sees this voltage on VBUS and erroneously reports `online=1` — it doesn't check whether the device is in source/host mode. Android interprets this as "charger connected" and shows a charging notification, even though the device is *providing* power, not receiving it.

## The Fix

A kernel module (`fix_charger.ko`) uses [kprobes](https://www.kernel.org/doc/html/latest/trace/kprobes.html) to install six probes:

| # | Target function | Module | Action |
|---|---|---|---|
| 1 | `eta6965_dump_register` | `eta6965_charger` | Skip (return 0) — eliminates I2C busy loop |
| 2 | `eta6965_dump_register` | `eta6965_charger_sec` | Skip (return 0) — eliminates I2C busy loop |
| 3 | `eta6965_enable_vbus` | `eta6965_charger` | Set OTG flag — tracks when VBUS power is enabled |
| 4 | `eta6965_disable_vbus` | `eta6965_charger` | Clear OTG flag — tracks when VBUS power is disabled |
| 5 | `eta6965_charger_get_property` | `eta6965_charger` | When OTG flag is set and property is `POWER_SUPPLY_PROP_ONLINE`, override to return 0 (not charging) |
| 6 | `mtk_charger_external_power_changed` | `mtk_charger_framework` | Throttle to 1-in-60 calls — breaks the charger thread feedback loop |

The charger thread continues to run on its normal 2-second timer for battery management — it just stops doing the useless I2C register dumps and the feedback loop that wakes it 30x/second. Real charger detection (actual USB charger plugged in) is unaffected.

### Boot-time address resolution

Because KASLR randomizes kernel module addresses on every boot, the Magisk `service.sh` script:

1. Temporarily sets `kptr_restrict=0` to read `/proc/kallsyms`
2. Resolves all six function addresses (filtering by module name to handle duplicate symbols)
3. Patches the addresses into the `.ko` template at fixed byte offsets
4. Loads the patched module with `insmod`

## Installation

**Requires**: Magisk, firmware v2.112.5.92(1204), kernel `5.10.233-android12-9-00062-g49c66df526b8-ab13101360`

1. Download `fix_charger_magisk_v1.4.zip` from [Releases](../../releases)
2. Open Magisk → Modules → Install from storage
3. Select the ZIP
4. Reboot

The installer will abort if the kernel version doesn't match.

To verify: check `/data/local/tmp/fix_charger.log` after boot, or run `lsmod | grep fix_charger`.

To uninstall: Magisk → Modules → remove "Fix Charger Thread CPU" → reboot.

## Building from Source

### Prerequisites

- Android NDK (tested with r27b)
- Python 3
- `adb` with root access to the target device

### Compile

```bash
# Set your NDK path
NDK=/path/to/android-ndk

# Compile
$NDK/toolchains/llvm/prebuilt/*/bin/clang \
    --target=aarch64-linux-gnu \
    -nostdinc -nostdlib \
    -D__KERNEL__ -DMODULE -DKBUILD_MODNAME='"fix_charger"' \
    -fno-builtin -fno-common -fno-stack-protector -fno-pic \
    -O2 -c fix_charger.c -o fix_charger.o

# Strip unnecessary sections
$NDK/toolchains/llvm/prebuilt/*/bin/llvm-objcopy \
    --remove-section=.eh_frame \
    --remove-section=.rela.eh_frame \
    --remove-section=.llvm_addrsig \
    --remove-section=.comment \
    --remove-section=.note.GNU-stack \
    fix_charger.o fix_charger_stripped.o

# Post-process into .ko (adds .plt, .gnu.linkonce.this_module, etc.)
python build_ko.py
```

The output `fix_charger.ko` contains marker addresses (`0xFEEDFACECAFEBABE`, `0xDEADC0DEBEEFCAFE`, `0xCAFEBABE12345678`, `0xDEADBEEF87654321`, `0xBAADF00DDEADBEEF`, `0x1234ABCD5678EF01`) that get patched with real kernel addresses by `service.sh` at boot.

### MODVERSIONS

The `.ko` includes CRC entries that must match the running kernel. If targeting a different firmware version, extract the correct CRCs from `/proc/kallsyms` or the kernel's `Module.symvers` and update the `____versions` array in `fix_charger.c`.

### Rebuild the Magisk ZIP

```bash
cd magisk_module
cp ../fix_charger.ko .
zip -r ../fix_charger_magisk_v1.4.zip \
    META-INF/ module.prop customize.sh service.sh fix_charger.ko
```

## Changelog

### v1.4
- Re-add `external_power_changed` throttle (dump_register skips alone insufficient without it)
- 6 kprobes total

### v1.3
- Fix false "Charging" notification during USB-C OTG (3 new kprobes: enable_vbus, disable_vbus, get_property)
- Removed `external_power_changed` throttle from v1.1 (incorrectly believed dump_register skips alone fix CPU)

### v1.1
- Added throttle on `mtk_charger_external_power_changed` (later removed in v1.3)

### v1.0
- Initial release — skip `eta6965_dump_register` in both charger modules

## Files

| File | Description |
|---|---|
| `fix_charger.c` | Kernel module source — self-contained, no kernel headers needed |
| `build_ko.py` | ELF post-processor that converts stripped `.o` into a loadable `.ko` |
| `magisk_module/` | Ready-to-install Magisk module |
| `magisk_module/service.sh` | Boot script — resolves KASLR addresses and loads patched module |
| `magisk_module/fix_charger.ko` | Prebuilt `.ko` template with marker addresses |
| `magisk_module/customize.sh` | Install-time kernel version check |

## License

GPL-2.0 (required for kernel modules using kprobes)
