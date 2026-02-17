#!/system/bin/sh
# fix_charger Magisk service script
# Resolves kernel addresses at boot time and patches fix_charger.ko before loading.
#
# Three kprobes:
# 1. eta6965_dump_register in eta6965_charger (skip I2C register dump)
# 2. eta6965_dump_register in eta6965_charger_sec (skip I2C register dump)
# 3. mtk_charger_external_power_changed (throttle feedback wakeup loop)

MODDIR="${0%/*}"
KO_TEMPLATE="$MODDIR/fix_charger.ko"
KO_PATCHED="/data/local/tmp/fix_charger_patched.ko"
LOG="/data/local/tmp/fix_charger.log"

# Fixed offsets of address markers in fix_charger.ko (from build)
OFFSET1=2008   # FEEDFACECAFEBABE - eta6965_charger:dump_register
OFFSET2=2144   # DEADC0DEBEEFCAFE - eta6965_charger_sec:dump_register
OFFSET3=2280   # BADDF00DCAFEF00D - mtk_charger_framework:external_power_changed

log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') $1" >> "$LOG"
}

log "=== fix_charger service starting ==="

# Wait for charger modules to be loaded
sleep 10

# Temporarily allow reading kernel symbol addresses
echo 0 > /proc/sys/kernel/kptr_restrict

# Resolve addresses from kallsyms
ADDR1=$(cat /proc/kallsyms | grep -w 'eta6965_dump_register' | grep '\[eta6965_charger\]$' | head -1 | awk '{print $1}')
ADDR2=$(cat /proc/kallsyms | grep -w 'eta6965_dump_register' | grep '\[eta6965_charger_sec\]$' | head -1 | awk '{print $1}')
ADDR3=$(cat /proc/kallsyms | grep -w 'mtk_charger_external_power_changed' | grep '\[mtk_charger_framework\]$' | head -1 | awk '{print $1}')

# Restore kptr_restrict
echo 2 > /proc/sys/kernel/kptr_restrict

log "ADDR1 (dump_register/charger): $ADDR1"
log "ADDR2 (dump_register/charger_sec): $ADDR2"
log "ADDR3 (ext_power_changed): $ADDR3"

# Validate all addresses were found
if [ -z "$ADDR1" ] || [ -z "$ADDR2" ] || [ -z "$ADDR3" ]; then
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

patch_addr "$KO_PATCHED" "$OFFSET1" "$ADDR1" "dump_register/charger"
patch_addr "$KO_PATCHED" "$OFFSET2" "$ADDR2" "dump_register/charger_sec"
patch_addr "$KO_PATCHED" "$OFFSET3" "$ADDR3" "ext_power_changed"

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
