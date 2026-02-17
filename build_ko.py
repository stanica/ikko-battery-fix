#!/usr/bin/env python3
"""
Post-process fix_charger_stripped.o into a proper .ko by adding:
- .plt and .init.plt (required by CONFIG_ARM64_MODULE_PLTS)
- .gnu.linkonce.this_module section (struct module, 0x400 bytes)
- .rela.gnu.linkonce.this_module (relocations for init/exit pointers)
- .note.Linux section
- Dedicated .shstrtab (separate from .strtab)
"""

import struct
import sys

SHT_NULL = 0
SHT_PROGBITS = 1
SHT_SYMTAB = 2
SHT_STRTAB = 3
SHT_RELA = 4
SHT_NOTE = 7
SHF_WRITE = 1
SHF_ALLOC = 2
SHF_EXECINSTR = 4
SHF_INFO_LINK = 0x40
R_AARCH64_ABS64 = 257
SHENTSIZE = 64  # sizeof(Elf64_Shdr)


def read_file(path):
    with open(path, 'rb') as f:
        return bytearray(f.read())


def write_file(path, data):
    with open(path, 'wb') as f:
        f.write(data)


def get_string(data, offset):
    end = data.index(0, offset)
    return data[offset:end].decode()


class StringTable:
    """Build a NUL-terminated string table."""
    def __init__(self):
        self.data = bytearray(b'\x00')  # starts with NUL
        self.strings = {'': 0}

    def add(self, s):
        if s in self.strings:
            return self.strings[s]
        off = len(self.data)
        self.data.extend(s.encode() + b'\x00')
        self.strings[s] = off
        return off

    def __len__(self):
        return len(self.data)

    def __bytes__(self):
        return bytes(self.data)


def make_shdr(name_off, sh_type, flags=0, size=0, link=0, info=0,
              addralign=1, entsize=0):
    """Create a 64-byte section header."""
    sh = bytearray(SHENTSIZE)
    struct.pack_into('<I', sh, 0x00, name_off)     # sh_name
    struct.pack_into('<I', sh, 0x04, sh_type)      # sh_type
    struct.pack_into('<Q', sh, 0x08, flags)        # sh_flags
    # sh_addr = 0 (0x10)
    # sh_offset filled later (0x18)
    struct.pack_into('<Q', sh, 0x20, size)         # sh_size
    struct.pack_into('<I', sh, 0x28, link)         # sh_link
    struct.pack_into('<I', sh, 0x2C, info)         # sh_info
    struct.pack_into('<Q', sh, 0x30, addralign)    # sh_addralign
    struct.pack_into('<Q', sh, 0x38, entsize)      # sh_entsize
    return sh


def align_up(n, alignment):
    return (n + alignment - 1) & ~(alignment - 1)


def main():
    data = read_file('fix_charger_stripped.o')
    file_len = len(data)

    # Parse ELF header
    assert data[:4] == b'\x7fELF', "Not an ELF file"
    assert data[4] == 2, "Not ELF64"
    assert struct.unpack_from('<H', data, 0x12)[0] == 0xB7, "Not AArch64"

    e_shoff = struct.unpack_from('<Q', data, 0x28)[0]
    e_shentsize = struct.unpack_from('<H', data, 0x3A)[0]
    e_shnum = struct.unpack_from('<H', data, 0x3C)[0]
    e_shstrndx = struct.unpack_from('<H', data, 0x3E)[0]
    assert e_shentsize == SHENTSIZE

    print(f"Input: {e_shnum} sections, e_shstrndx={e_shstrndx}")

    # Read input section headers
    in_shdrs = []
    for i in range(e_shnum):
        off = e_shoff + i * SHENTSIZE
        in_shdrs.append(bytearray(data[off:off + SHENTSIZE]))

    # Read input shstrtab (for reading section names)
    shstr_sh = in_shdrs[e_shstrndx]
    shstr_off = struct.unpack_from('<Q', shstr_sh, 0x18)[0]
    shstr_size = struct.unpack_from('<Q', shstr_sh, 0x20)[0]
    in_shstrtab = data[shstr_off:shstr_off + shstr_size]

    # Find symtab
    symtab_idx = None
    strtab_idx = None
    for i, sh in enumerate(in_shdrs):
        if struct.unpack_from('<I', sh, 4)[0] == SHT_SYMTAB:
            symtab_idx = i
            strtab_idx = struct.unpack_from('<I', sh, 0x28)[0]
    assert symtab_idx is not None, "No symtab found"

    # Read symbol string table
    str_sh = in_shdrs[strtab_idx]
    str_off = struct.unpack_from('<Q', str_sh, 0x18)[0]
    str_size = struct.unpack_from('<Q', str_sh, 0x20)[0]
    in_strtab = data[str_off:str_off + str_size]

    # Read symbol table to find init_module and cleanup_module
    sym_sh = in_shdrs[symtab_idx]
    sym_off = struct.unpack_from('<Q', sym_sh, 0x18)[0]
    sym_size = struct.unpack_from('<Q', sym_sh, 0x20)[0]
    num_syms = sym_size // 24

    init_sym_idx = None
    cleanup_sym_idx = None
    for i in range(num_syms):
        s = sym_off + i * 24
        st_name = struct.unpack_from('<I', data, s)[0]
        name = get_string(in_strtab, st_name)
        if name == 'init_module':
            init_sym_idx = i
        elif name == 'cleanup_module':
            cleanup_sym_idx = i

    assert init_sym_idx is not None, "init_module symbol not found"
    assert cleanup_sym_idx is not None, "cleanup_module symbol not found"
    print(f"init_module: sym {init_sym_idx}, cleanup_module: sym {cleanup_sym_idx}")

    # Collect input section names and data
    in_names = []
    in_data = []
    for i, sh in enumerate(in_shdrs):
        sh_name_off = struct.unpack_from('<I', sh, 0)[0]
        name = get_string(in_shstrtab, sh_name_off) if sh_name_off < len(in_shstrtab) else ''
        in_names.append(name)

        sh_type = struct.unpack_from('<I', sh, 4)[0]
        sh_off = struct.unpack_from('<Q', sh, 0x18)[0]
        sh_sz = struct.unpack_from('<Q', sh, 0x20)[0]
        if sh_type == SHT_NULL or sh_sz == 0:
            in_data.append(bytearray())
        else:
            in_data.append(bytearray(data[sh_off:sh_off + sh_sz]))

    # Build new section header string table with ALL section names
    shstrtab = StringTable()
    # Add all existing section names
    name_offsets = {}
    for name in in_names:
        name_offsets[name] = shstrtab.add(name)
    # Add new section names
    name_offsets['.plt'] = shstrtab.add('.plt')
    name_offsets['.init.plt'] = shstrtab.add('.init.plt')
    name_offsets['.gnu.linkonce.this_module'] = shstrtab.add('.gnu.linkonce.this_module')
    name_offsets['.rela.gnu.linkonce.this_module'] = shstrtab.add('.rela.gnu.linkonce.this_module')
    name_offsets['.note.Linux'] = shstrtab.add('.note.Linux')
    name_offsets['.shstrtab'] = shstrtab.add('.shstrtab')

    # Plan output sections: input sections (minus old shstrtab reuse) + new sections + .shstrtab
    # Output section layout:
    #   [0..N-1] = input sections (keep indices stable)
    #   [N]   = .plt
    #   [N+1] = .init.plt
    #   [N+2] = .gnu.linkonce.this_module
    #   [N+3] = .rela.gnu.linkonce.this_module
    #   [N+4] = .note.Linux
    #   [N+5] = .shstrtab (NEW dedicated section)

    N = e_shnum  # number of input sections
    plt_idx = N
    init_plt_idx = N + 1
    this_module_idx = N + 2
    rela_this_idx = N + 3
    note_linux_idx = N + 4
    shstrtab_idx = N + 5
    total_sections = N + 6

    print(f"Output: {total_sections} sections, shstrtab at {shstrtab_idx}")

    # Create new section data

    # .plt (1 byte placeholder, kernel will expand it)
    plt_data = bytearray(1)

    # .init.plt (1 byte placeholder)
    init_plt_data = bytearray(1)

    # .gnu.linkonce.this_module (struct module = 0x400 bytes)
    this_module_data = bytearray(0x400)
    mod_name = b'fix_charger'
    this_module_data[0x18:0x18 + len(mod_name)] = mod_name

    # .rela.gnu.linkonce.this_module (2 RELA entries)
    rela_data = bytearray(48)
    struct.pack_into('<Q', rela_data, 0, 0x190)                     # r_offset: init_module
    struct.pack_into('<Q', rela_data, 8, (init_sym_idx << 32) | R_AARCH64_ABS64)
    struct.pack_into('<q', rela_data, 16, 0)                         # r_addend
    struct.pack_into('<Q', rela_data, 24, 0x3c0)                    # r_offset: cleanup_module
    struct.pack_into('<Q', rela_data, 32, (cleanup_sym_idx << 32) | R_AARCH64_ABS64)
    struct.pack_into('<q', rela_data, 40, 0)                         # r_addend

    # .note.Linux
    note_data = bytearray()
    note_data += struct.pack('<III', 6, 1, 0x100)  # namesz=6, descsz=1, type=0x100
    note_data += b'Linux\x00\x00\x00'              # name padded to 4-byte boundary (8 bytes)
    note_data += struct.pack('<I', 0)               # desc padded to 4 bytes
    # Total: 12 + 8 + 4 = 24 = 0x18

    # .shstrtab data
    shstrtab_data = bytearray(bytes(shstrtab))

    # Build output section headers and data list
    out_shdrs = []
    out_data = []

    # Copy input sections with updated sh_name offsets
    for i in range(N):
        sh = bytearray(in_shdrs[i])
        # Update sh_name to point into new shstrtab
        name = in_names[i]
        struct.pack_into('<I', sh, 0, name_offsets.get(name, 0))
        out_shdrs.append(sh)
        out_data.append(in_data[i])

    # .plt
    out_shdrs.append(make_shdr(
        name_offsets['.plt'], SHT_PROGBITS,
        flags=SHF_ALLOC | SHF_EXECINSTR,
        size=len(plt_data), addralign=16))
    out_data.append(plt_data)

    # .init.plt
    out_shdrs.append(make_shdr(
        name_offsets['.init.plt'], SHT_PROGBITS,
        flags=SHF_ALLOC | SHF_EXECINSTR,
        size=len(init_plt_data), addralign=1))
    out_data.append(init_plt_data)

    # .gnu.linkonce.this_module
    out_shdrs.append(make_shdr(
        name_offsets['.gnu.linkonce.this_module'], SHT_PROGBITS,
        flags=SHF_ALLOC | SHF_WRITE,
        size=len(this_module_data), addralign=64))
    out_data.append(this_module_data)

    # .rela.gnu.linkonce.this_module
    out_shdrs.append(make_shdr(
        name_offsets['.rela.gnu.linkonce.this_module'], SHT_RELA,
        flags=SHF_INFO_LINK,
        size=len(rela_data), link=symtab_idx, info=this_module_idx,
        addralign=8, entsize=24))
    out_data.append(rela_data)

    # .note.Linux
    out_shdrs.append(make_shdr(
        name_offsets['.note.Linux'], SHT_NOTE,
        flags=SHF_ALLOC,
        size=len(note_data), addralign=4))
    out_data.append(note_data)

    # .shstrtab
    out_shdrs.append(make_shdr(
        name_offsets['.shstrtab'], SHT_STRTAB,
        size=len(shstrtab_data), addralign=1))
    out_data.append(shstrtab_data)

    assert len(out_shdrs) == total_sections
    assert len(out_data) == total_sections

    # Assemble the output ELF
    elf = bytearray(data[:0x40])  # Copy ELF header

    # Update ELF header fields
    struct.pack_into('<H', elf, 0x3C, total_sections)   # e_shnum
    struct.pack_into('<H', elf, 0x3E, shstrtab_idx)     # e_shstrndx

    # Write section data, tracking offsets
    offsets = [0] * total_sections
    for i in range(total_sections):
        d = out_data[i]
        if len(d) == 0:
            continue
        # Align to section's alignment requirement
        sh_align = struct.unpack_from('<Q', out_shdrs[i], 0x30)[0]
        if sh_align < 1:
            sh_align = 1
        target = align_up(len(elf), max(sh_align, 4))  # min 4-byte alignment
        while len(elf) < target:
            elf.append(0)
        offsets[i] = len(elf)
        elf.extend(d)

    # Align for section header table (8-byte alignment)
    while len(elf) % 8 != 0:
        elf.append(0)
    sh_table_off = len(elf)
    struct.pack_into('<Q', elf, 0x28, sh_table_off)  # e_shoff

    # Write section headers with correct offsets
    for i in range(total_sections):
        sh = bytearray(out_shdrs[i])
        if offsets[i] > 0:
            struct.pack_into('<Q', sh, 0x18, offsets[i])
        elif len(out_data[i]) == 0:
            sh_type = struct.unpack_from('<I', sh, 4)[0]
            if sh_type != SHT_NULL:
                struct.pack_into('<Q', sh, 0x18, 0)
                struct.pack_into('<Q', sh, 0x20, 0)
        elf.extend(sh)

    # Validation: check what the kernel checks
    print("\n--- Validation ---")
    total_file_len = len(elf)

    # Re-read our output
    out_shoff = struct.unpack_from('<Q', elf, 0x28)[0]
    out_shnum = struct.unpack_from('<H', elf, 0x3C)[0]
    out_shstrndx = struct.unpack_from('<H', elf, 0x3E)[0]

    # Check section header table fits
    assert out_shoff + out_shnum * SHENTSIZE <= total_file_len, \
        f"Section headers overflow: {out_shoff} + {out_shnum}*64 > {total_file_len}"

    # Check e_shstrndx is valid
    assert out_shstrndx > 0 and out_shstrndx < out_shnum, \
        f"Invalid e_shstrndx: {out_shstrndx}"

    # Read output shstrtab
    shstr_sh_off = out_shoff + out_shstrndx * SHENTSIZE
    vshstr_off = struct.unpack_from('<Q', elf, shstr_sh_off + 0x18)[0]
    vshstr_sz = struct.unpack_from('<Q', elf, shstr_sh_off + 0x20)[0]

    # Check shstrtab NUL-termination
    assert vshstr_sz >= 2, f"shstrtab too small: {vshstr_sz}"
    assert vshstr_off + vshstr_sz <= total_file_len, \
        f"shstrtab overflows: {vshstr_off}+{vshstr_sz} > {total_file_len}"
    assert elf[vshstr_off + vshstr_sz - 1] == 0, \
        f"shstrtab not NUL-terminated"
    print(f"  shstrtab: offset={vshstr_off:#x}, size={vshstr_sz}, NUL-terminated=YES")

    # Validate each section
    errors = 0
    for i in range(1, out_shnum):
        sh_off_pos = out_shoff + i * SHENTSIZE
        sh_type = struct.unpack_from('<I', elf, sh_off_pos + 4)[0]
        sh_offset = struct.unpack_from('<Q', elf, sh_off_pos + 0x18)[0]
        sh_size = struct.unpack_from('<Q', elf, sh_off_pos + 0x20)[0]
        sh_link = struct.unpack_from('<I', elf, sh_off_pos + 0x28)[0]
        sh_info = struct.unpack_from('<I', elf, sh_off_pos + 0x2C)[0]
        sh_name_off = struct.unpack_from('<I', elf, sh_off_pos)[0]

        # Get section name
        if sh_name_off < vshstr_sz:
            sec_name = get_string(elf[vshstr_off:vshstr_off + vshstr_sz], sh_name_off)
        else:
            sec_name = f"<invalid name offset {sh_name_off}>"
            errors += 1

        # Check sh_name within shstrtab
        if sh_name_off >= vshstr_sz:
            print(f"  [{i}] {sec_name}: sh_name={sh_name_off} >= shstrtab size {vshstr_sz} ERROR")
            errors += 1

        # Check offset+size within file
        secend = sh_offset + sh_size
        if secend < sh_offset:
            print(f"  [{i}] {sec_name}: offset overflow ERROR")
            errors += 1
        if secend > total_file_len:
            print(f"  [{i}] {sec_name}: off={sh_offset:#x}+size={sh_size:#x}={secend:#x} > {total_file_len:#x} ERROR")
            errors += 1

        # Check sh_link
        if sh_link > 0 and sh_link >= out_shnum:
            print(f"  [{i}] {sec_name}: sh_link={sh_link} >= shnum={out_shnum} ERROR")
            errors += 1

        # Check sh_info for RELA sections
        if sh_type == SHT_RELA and sh_info >= out_shnum:
            print(f"  [{i}] {sec_name}: sh_info={sh_info} >= shnum={out_shnum} ERROR")
            errors += 1

    if errors > 0:
        print(f"\n  VALIDATION FAILED with {errors} errors!")
        sys.exit(1)
    else:
        print(f"  All {out_shnum} sections valid")

    write_file('fix_charger.ko', elf)
    print(f"\nBuilt fix_charger.ko: {total_file_len} bytes, {out_shnum} sections")


if __name__ == '__main__':
    main()
