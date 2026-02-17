#!/system/bin/sh
# Magisk module install-time script

EXPECTED_KERNEL="5.10.233-android12-9-00062-g49c66df526b8-ab13101360"
ACTUAL_KERNEL="$(uname -r)"

ui_print "- Checking kernel compatibility..."
ui_print "  Expected: $EXPECTED_KERNEL"
ui_print "  Found:    $ACTUAL_KERNEL"

if [ "$ACTUAL_KERNEL" != "$EXPECTED_KERNEL" ]; then
  ui_print ""
  ui_print "***ERROR: Kernel mismatch!***"
  ui_print "This module only works on IKKO firmware v2.112.5.92(1204)"
  ui_print "with kernel $EXPECTED_KERNEL"
  abort "Installation aborted - wrong kernel version"
fi

ui_print "- Kernel matches, installing..."

# Set executable permission on service script
set_perm "$MODPATH/service.sh" 0 0 0755
