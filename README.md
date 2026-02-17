# IKKO Battery Fix

Magisk module that fixes a kernel bug causing 25% CPU usage on IKKO devices with the ETA6965 charger IC (MediaTek MT6789).

## The Problem

Three functions in the charger driver stack run in a tight feedback loop ~30 times per second:

1. **`eta6965_dump_register`** (in `eta6965_charger`) — reads all I2C registers from the charger IC and logs them via `printk`. Nothing consumes this data. Debug code left in production.

2. **`eta6965_dump_register`** (in `eta6965_charger_sec`) — same function duplicated in the secondary charger driver.

3. **`mtk_charger_external_power_changed`** (in `mtk_charger_framework`) — callback that fires when power supply properties change. The charger thread updates properties on every cycle, which triggers this callback, which wakes the charger thread, creating an infinite feedback loop.

The result: `charger_thread` burns ~25% of one CPU core continuously, even with the screen off and no charger connected.

## The Fix

A kernel module (`fix_charger.ko`) uses [kprobes](https://www.kernel.org/doc/html/latest/trace/kprobes.html) to intercept all three functions and make them return immediately. The charger thread continues to run on its normal 2-second timer for battery management — it just stops doing the useless busywork between cycles.

### Boot-time address resolution

Because KASLR randomizes kernel module addresses on every boot, the Magisk `service.sh` script:

1. Temporarily sets `kptr_restrict=0` to read `/proc/kallsyms`
2. Resolves the three function addresses (filtering by module name to handle the duplicate symbol)
3. Patches the addresses into the `.ko` template at fixed byte offsets
4. Loads the patched module with `insmod`

## Installation

**Requires**: Magisk, firmware v2.112.5.92(1204), kernel `5.10.233-android12-9-00062-g49c66df526b8-ab13101360`

1. Download `fix_charger_magisk_v1.0.zip` from [Releases](../../releases)
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

The output `fix_charger.ko` contains marker addresses (`0xFEEDFACECAFEBABE`, `0xDEADC0DEBEEFCAFE`, `0xBADDF00DCAFEF00D`) that get patched with real kernel addresses by `service.sh` at boot.

### MODVERSIONS

The `.ko` includes CRC entries that must match the running kernel. If targeting a different firmware version, extract the correct CRCs from `/proc/kallsyms` or the kernel's `Module.symvers` and update the `____versions` array in `fix_charger.c`.

### Rebuild the Magisk ZIP

```bash
cd magisk_module
cp ../fix_charger.ko .
zip -r ../fix_charger_magisk_v1.0.zip \
    META-INF/ module.prop customize.sh service.sh fix_charger.ko
```

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
