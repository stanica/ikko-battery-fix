// SPDX-License-Identifier: GPL-2.0
/*
 * fix_charger.ko - Fix charger_thread busy loop and OTG detection via kprobes
 * Self-contained: no kernel headers needed beyond compiler builtins
 *
 * Probes:
 * 1: eta6965_dump_register in eta6965_charger (skip I2C reg dump)
 * 2: eta6965_dump_register in eta6965_charger_sec (skip I2C reg dump)
 * 3: eta6965_enable_vbus in eta6965_charger (set OTG flag on plug)
 * 4: eta6965_disable_vbus in eta6965_charger (clear OTG flag on unplug)
 * 5: eta6965_charger_get_property in eta6965_charger (fix online during OTG)
 */

/* Minimal type definitions for arm64 kernel module */
typedef signed long long s64;
typedef unsigned long long u64;
typedef unsigned int u32;
typedef unsigned short u16;
typedef unsigned char u8;
typedef int bool;

/* pt_regs for arm64 - first 31 general regs + sp + pc + pstate */
struct pt_regs {
	u64 regs[31];
	u64 sp;
	u64 pc;
	u64 pstate;
};

/* Minimal kprobe structure matching kernel's layout for arm64 */
struct kprobe {
	/* struct hlist_node hlist */
	void *hlist_next;
	void **hlist_pprev;
	/* struct list_head list */
	void *list_next;
	void *list_prev;
	/* unsigned long nmissed */
	u64 nmissed;
	/* kprobe_opcode_t *addr */
	void *addr;
	/* const char *symbol_name */
	const char *symbol_name;
	/* unsigned int offset + padding */
	u32 offset;
	u32 _pad0;
	/* handlers */
	void *pre_handler;
	void *post_handler;
	void *fault_handler;
	/* kprobe_opcode_t opcode + padding */
	u32 opcode;
	u32 _pad1;
	/* struct arch_specific_insn (arch_probe_insn: 4 pointers) */
	void *ainsn[4];
	/* u32 flags + padding */
	u32 flags;
	u32 _pad2;
};

/* External kernel functions */
extern int register_kprobe(struct kprobe *p);
extern void unregister_kprobe(struct kprobe *p);
extern int printk(const char *fmt, ...);

/*
 * CFI (Control Flow Integrity) stub.
 * CONFIG_CFI_CLANG=y requires modules to export __cfi_check.
 */
void __cfi_check(u64 id, void *ptr, void *diag)
{
}

void __attribute__((weak)) __cfi_check_fail(void *data, void *ptr)
{
}

/* Kprobe pre-handler: skip the function entirely, return 0 to caller */
static int skip_function(struct kprobe *p, struct pt_regs *regs)
{
	regs->regs[0] = 0;
	regs->pc = regs->regs[30];
	return 1;
}

/*
 * OTG VBUS state tracking.
 *
 * eta6965_enable_vbus and eta6965_disable_vbus are separate functions
 * called by rt-pd-manager when a USB-C OTG adapter is plugged/unplugged.
 * We track the state so fix_online_in_otg can suppress the bogus
 * "USB powered" report caused by the charger IC seeing its own VBUS output.
 */
static int otg_active;

static int set_otg_active(struct kprobe *p, struct pt_regs *regs)
{
	otg_active = 1;
	return 0; /* let eta6965_enable_vbus execute normally */
}

static int clear_otg_active(struct kprobe *p, struct pt_regs *regs)
{
	otg_active = 0;
	return 0; /* let eta6965_disable_vbus execute normally */
}

/*
 * Fix "USB powered: true" during OTG.
 *
 * eta6965_charger_get_property(struct power_supply *psy,
 *     enum power_supply_property psp, union power_supply_propval *val)
 *   x0 = psy, x1 = property enum, x2 = &val->intval
 *
 * POWER_SUPPLY_PROP_ONLINE = 4 in Linux 5.10 (stable since 2.6.x).
 * When OTG is active, the charger IC hardware erroneously reports
 * VBUS present (because the device itself is generating 5V for OTG).
 * We override online=0 to prevent Android from showing "Charging".
 */
#define PSP_ONLINE 4

static int fix_online_in_otg(struct kprobe *p, struct pt_regs *regs)
{
	if (otg_active && regs->regs[1] == PSP_ONLINE) {
		*(u32 *)regs->regs[2] = 0;  /* val->intval = 0 */
		regs->regs[0] = 0;          /* return 0 (success) */
		regs->pc = regs->regs[30];  /* skip to caller */
		return 1;
	}
	return 0; /* let function run for all other properties */
}

/*
 * Address markers — patched by loader script with actual addresses from kallsyms.
 */
#define ADDR_MARKER_1 ((void *)0xFEEDFACECAFEBABEULL)  /* eta6965_charger: dump_register */
#define ADDR_MARKER_2 ((void *)0xDEADC0DEBEEFCAFEULL)  /* eta6965_charger_sec: dump_register */
#define ADDR_MARKER_3 ((void *)0xCAFEBABE12345678ULL)  /* eta6965_charger: enable_vbus */
#define ADDR_MARKER_4 ((void *)0xDEADBEEF87654321ULL)  /* eta6965_charger: disable_vbus */
#define ADDR_MARKER_5 ((void *)0xBAADF00DDEADBEEFULL)  /* eta6965_charger: get_property */

static struct kprobe kp_dump1 = {
	.addr = ADDR_MARKER_1,
	.pre_handler = (void *)skip_function,
};

static struct kprobe kp_dump2 = {
	.addr = ADDR_MARKER_2,
	.pre_handler = (void *)skip_function,
};

static struct kprobe kp_vbus_on = {
	.addr = ADDR_MARKER_3,
	.pre_handler = (void *)set_otg_active,
};

static struct kprobe kp_vbus_off = {
	.addr = ADDR_MARKER_4,
	.pre_handler = (void *)clear_otg_active,
};

static struct kprobe kp_prop = {
	.addr = ADDR_MARKER_5,
	.pre_handler = (void *)fix_online_in_otg,
};

#define NUM_KPROBES 5
static struct kprobe *all_kprobes[NUM_KPROBES];
static int num_registered;

int __attribute__((section(".init.text"))) init_module(void)
{
#ifdef TEST_ONLY
	printk("fix_charger: test load OK\n");
	return -22;
#else
	struct kprobe *probes[NUM_KPROBES] = {
		&kp_dump1, &kp_dump2, &kp_vbus_on, &kp_vbus_off, &kp_prop
	};
	int i, ret;

	num_registered = 0;
	for (i = 0; i < NUM_KPROBES; i++) {
		ret = register_kprobe(probes[i]);
		if (ret < 0) {
			printk("fix_charger: kprobe %d failed: %d\n", i, ret);
			/* Unregister all previously registered */
			while (num_registered > 0) {
				num_registered--;
				unregister_kprobe(all_kprobes[num_registered]);
			}
			return ret;
		}
		all_kprobes[num_registered++] = probes[i];
	}
	printk("fix_charger: patched %d functions\n", num_registered);
	return 0;
#endif
}

void __attribute__((section(".exit.text"))) cleanup_module(void)
{
	int i;
	for (i = 0; i < num_registered; i++)
		unregister_kprobe(all_kprobes[i]);
	printk("fix_charger: unpatched %d functions\n", num_registered);
}

/* Module metadata */
static const char __modinfo_license[] __attribute__((used, section(".modinfo"))) =
	"license=GPL";
static const char __modinfo_description[] __attribute__((used, section(".modinfo"))) =
	"description=Fix charger_thread busy loop and OTG charger detection";
static const char __modinfo_vermagic[] __attribute__((used, section(".modinfo"))) =
	"vermagic=5.10.233-android12-9-00062-g49c66df526b8-ab13101360 SMP preempt mod_unload modversions aarch64";
static const char __modinfo_name[] __attribute__((used, section(".modinfo"))) =
	"name=fix_charger";
static const char __modinfo_retpoline[] __attribute__((used, section(".modinfo"))) =
	"retpoline=Y";

/* MODVERSIONS CRC entries - must match kernel build ab13101360 */
struct modversion_info {
	u64 crc;
	char name[56];
};

static const struct modversion_info ____versions[]
__attribute__((used, section("__versions"))) = {
	{ 0x7c24b32d, "module_layout" },
	{ 0xc502eb6d, "register_kprobe" },
	{ 0x18b23dc5, "unregister_kprobe" },
	{ 0xc5850110, "printk" },
};
