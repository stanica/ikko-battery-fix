#!/system/bin/sh
# fix_charger Magisk service script
# Resolves kernel addresses at boot time and patches fix_charger.ko before loading.
#
# Six kprobes:
# 1. eta6965_dump_register in eta6965_charger (skip I2C register dump)
# 2. eta6965_dump_register in eta6965_charger_sec (skip I2C register dump)
# 3. eta6965_enable_vbus in eta6965_charger (set OTG flag on plug)
# 4. eta6965_disable_vbus in eta6965_charger (clear OTG flag on unplug)
# 5. eta6965_charger_get_property in eta6965_charger (fix online during OTG)
# 6. mtk_charger_external_power_changed in mtk_charger_framework (throttle feedback loop)

MODDIR="${0%/*}"
KO_TEMPLATE="$MODDIR/fix_charger.ko"
KO_PATCHED="/data/local/tmp/fix_charger_patched.ko"
LOG="/data/local/tmp/fix_charger.log"

# Fixed offsets of address markers in fix_charger.ko (from build)
OFFSET1=2824   # FEEDFACECAFEBABE - eta6965_charger:dump_register
OFFSET2=2960   # DEADC0DEBEEFCAFE - eta6965_charger_sec:dump_register
OFFSET3=3096   # CAFEBABE12345678 - eta6965_charger:enable_vbus
OFFSET4=3232   # DEADBEEF87654321 - eta6965_charger:disable_vbus
OFFSET5=3368   # BAADF00DDEADBEEF - eta6965_charger:get_property
OFFSET6=3504   # 1234ABCD5678EF01 - mtk_charger_framework:ext_power_changed

log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') $1" >> "$LOG"
}

log "=== fix_charger service starting ==="

# Wait for charger modules to be loaded
sleep 10

# Temporarily allow reading kernel symbol addresses
echo 0 > /proc/sys/kernel/kptr_restrict

# Resolve addresses from kallsyms
ADDR1=$(grep -w 'eta6965_dump_register' /proc/kallsyms | grep '\[eta6965_charger\]$' | head -1 | awk '{print $1}')
ADDR2=$(grep -w 'eta6965_dump_register' /proc/kallsyms | grep '\[eta6965_charger_sec\]$' | head -1 | awk '{print $1}')
ADDR3=$(grep -w 'eta6965_enable_vbus' /proc/kallsyms | grep '\[eta6965_charger\]$' | head -1 | awk '{print $1}')
ADDR4=$(grep -w 'eta6965_disable_vbus' /proc/kallsyms | grep '\[eta6965_charger\]$' | head -1 | awk '{print $1}')
ADDR5=$(grep -w 'eta6965_charger_get_property' /proc/kallsyms | grep '\[eta6965_charger\]$' | head -1 | awk '{print $1}')
ADDR6=$(grep -w 'mtk_charger_external_power_changed' /proc/kallsyms | grep '\[mtk_charger_framework\]$' | head -1 | awk '{print $1}')

# Restore kptr_restrict
echo 2 > /proc/sys/kernel/kptr_restrict

log "ADDR1 (dump_register/charger): $ADDR1"
log "ADDR2 (dump_register/charger_sec): $ADDR2"
log "ADDR3 (enable_vbus): $ADDR3"
log "ADDR4 (disable_vbus): $ADDR4"
log "ADDR5 (get_property): $ADDR5"
log "ADDR6 (ext_power_changed): $ADDR6"

# Validate all addresses were found
if [ -z "$ADDR1" ] || [ -z "$ADDR2" ] || [ -z "$ADDR3" ] || [ -z "$ADDR4" ] || [ -z "$ADDR5" ] || [ -z "$ADDR6" ]; then
    log "ERROR: Failed to resolve one or more addresses, aborting"
    exit 1
fi

# Copy template
cp "$KO_TEMPLATE" "$KO_PATCHED"

# Patch a single 8-byte address at a fixed offset
# Usage: patch_addr <file> <byte_offset> <16-char-hex-address> <description>
patch_addr() {
    local file="$1"
    local offset="$2"
    local addr="$3"
    local desc="$4"

    # Convert 16-char hex address to 8 little-endian bytes
    local b0=$(echo "$addr" | cut -c15-16)
    local b1=$(echo "$addr" | cut -c13-14)
    local b2=$(echo "$addr" | cut -c11-12)
    local b3=$(echo "$addr" | cut -c9-10)
    local b4=$(echo "$addr" | cut -c7-8)
    local b5=$(echo "$addr" | cut -c5-6)
    local b6=$(echo "$addr" | cut -c3-4)
    local b7=$(echo "$addr" | cut -c1-2)

    printf "\\x${b0}\\x${b1}\\x${b2}\\x${b3}\\x${b4}\\x${b5}\\x${b6}\\x${b7}" | \
        dd of="$file" bs=1 seek="$offset" conv=notrunc 2>/dev/null

    log "Patched $desc at offset $offset"
}

# Pad addresses to 16 hex chars
ADDR1=$(printf '%016s' "$ADDR1")
ADDR2=$(printf '%016s' "$ADDR2")
ADDR3=$(printf '%016s' "$ADDR3")
ADDR4=$(printf '%016s' "$ADDR4")
ADDR5=$(printf '%016s' "$ADDR5")
ADDR6=$(printf '%016s' "$ADDR6")

patch_addr "$KO_PATCHED" "$OFFSET1" "$ADDR1" "dump_register/charger"
patch_addr "$KO_PATCHED" "$OFFSET2" "$ADDR2" "dump_register/charger_sec"
patch_addr "$KO_PATCHED" "$OFFSET3" "$ADDR3" "enable_vbus"
patch_addr "$KO_PATCHED" "$OFFSET4" "$ADDR4" "disable_vbus"
patch_addr "$KO_PATCHED" "$OFFSET5" "$ADDR5" "get_property"
patch_addr "$KO_PATCHED" "$OFFSET6" "$ADDR6" "ext_power_changed"

# Load the patched module
insmod "$KO_PATCHED"
ret=$?

if [ $ret -eq 0 ]; then
    log "Module loaded successfully"
else
    log "ERROR: insmod failed with code $ret"
fi

# Cleanup patched temp file
rm -f "$KO_PATCHED"

log "=== fix_charger service done ==="
