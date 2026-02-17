// SPDX-License-Identifier: GPL-2.0
/*
 * fix_charger.ko - Fix charger_thread busy loop via kprobes
 * Self-contained: no kernel headers needed beyond compiler builtins
 *
 * Probes:
 * 1-2: eta6965_dump_register in both charger modules (skip I2C reg dump)
 * 3:   mtk_charger_external_power_changed (stop feedback wakeup loop)
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
 * Throttled pre-handler for mtk_charger_external_power_changed.
 * Allow the function to execute once every ~60 calls (~2 seconds at
 * 30 calls/sec feedback rate). This breaks the busy loop while still
 * allowing real charger plug/unplug events to be detected promptly.
 */
static int throttle_count;

static int throttle_function(struct kprobe *p, struct pt_regs *regs)
{
	if (++throttle_count >= 60) {
		throttle_count = 0;
		return 0; /* let it run */
	}
	regs->regs[0] = 0;
	regs->pc = regs->regs[30];
	return 1; /* skip */
}

/*
 * Address markers — patched by loader script with actual addresses from kallsyms.
 * This is necessary because:
 * - eta6965_dump_register exists in TWO modules, kallsyms_lookup_name returns wrong one
 * - mtk_charger_external_power_changed is unique but we use addr for consistency
 */
#define ADDR_MARKER_1 ((void *)0xFEEDFACECAFEBABEULL)  /* eta6965_charger: dump_register */
#define ADDR_MARKER_2 ((void *)0xDEADC0DEBEEFCAFEULL)  /* eta6965_charger_sec: dump_register */
#define ADDR_MARKER_3 ((void *)0xBADDF00DCAFEF00DULL)  /* mtk_charger_framework: external_power_changed */

static struct kprobe kp_dump1 = {
	.addr = ADDR_MARKER_1,
	.pre_handler = (void *)skip_function,
};

static struct kprobe kp_dump2 = {
	.addr = ADDR_MARKER_2,
	.pre_handler = (void *)skip_function,
};

static struct kprobe kp_pwr = {
	.addr = ADDR_MARKER_3,
	.pre_handler = (void *)throttle_function,
};

#define NUM_KPROBES 3
static struct kprobe *all_kprobes[NUM_KPROBES];
static int num_registered;

int __attribute__((section(".init.text"))) init_module(void)
{
#ifdef TEST_ONLY
	printk("fix_charger: test load OK\n");
	return -22;
#else
	struct kprobe *probes[NUM_KPROBES] = { &kp_dump1, &kp_dump2, &kp_pwr };
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
	"description=Fix charger_thread busy loop by skipping dump_register and throttling wakeups";
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
