// Position-independent, stackless probe; build.py supplies fixed physical ranges.
.macro reject_overlap low, high, other_low, other_high
    cmp \low, \other_high
    b.hs .Lclear\@
    cmp \other_low, \high
    b.lo blocked
.Lclear\@:
.endm

.macro flush start, end, cursor, line, scratch
    mrs \scratch, ctr_el0
    ubfx \scratch, \scratch, #16, #4
    mov \line, #4
    lsl \line, \line, \scratch
    sub \scratch, \line, #1
    bic \cursor, \start, \scratch
.Lflush\@:
    dc cvac, \cursor
    add \cursor, \cursor, \line
    cmp \cursor, \end
    b.lo .Lflush\@
    dsb sy
.endm

// The vendor header places data at +12: every payload access uses 32-bit alignment.
.macro store64 value, base, offset
    str w\value, [\base, #\offset]
    lsr x14, x\value, #32
    str w14, [\base, #(\offset + 4)]
.endm

// IEEE CRC-32 of the 128-byte body; the trailing reserved word remains zero.
.macro checksum base
    mov w10, #-1
    mov x11, \base
    add x12, \base, #128
    mov w14, #0x8320
    movk w14, #0xedb8, lsl #16
.Lbyte\@:
    ldrb w13, [x11], #1
    eor w10, w10, w13
    mov w13, #8
.Lbit\@:
    tst w10, #1
    lsr w10, w10, #1
    b.eq .Lnext\@
    eor w10, w10, w14
.Lnext\@:
    subs w13, w13, #1
    b.ne .Lbit\@
    cmp x11, x12
    b.lo .Lbyte\@
    mvn w10, w10
    str w10, [\base, #128]
.endm

.section .text
.global _start, entry, blocked, stage2, stage2_end, image_end, reset_call, target_halt
_start:
    b entry
    .word 0
    .quad 0
    .quad image_end - _start
    .quad 0xa
    .quad 0, 0, 0
    .ascii "ARM\x64"
    .word 0
entry:
    mov x20, x0
    mov x21, x1
    mov x22, x2
    mov x23, x3
    mrs x24, CurrentEL
    mrs x26, daif
    cmp x24, #4
    b.eq el1
    cmp x24, #8
    b.eq el2
    cmp x24, #12
    b.ne blocked
    mrs x25, sctlr_el3
    b state_ready
el2:
    mrs x25, sctlr_el2
    b state_ready
el1:
    mrs x25, sctlr_el1
state_ready:
    tbnz x25, #0, blocked
    tbnz x25, #25, blocked
#if RESET_PSCI
    cmp x24, #12
    b.eq blocked
#endif
    msr daifset, #15
    adr x27, _start
    adr x28, stage2
    adr x4, image_end
    mov x5, #CONSOLE_ADDRESS
    mov x6, #CONSOLE_END
    reject_overlap x27, x4, x5, x6
    // ABL supplies a DTB in x0. Reserve 2 MiB without dereferencing it.
    adds x7, x20, #0x200, lsl #12
    b.cs blocked
    reject_overlap x20, x7, x5, x6
    reject_overlap x27, x4, x20, x7

    // Invalidate the previous header before replacing its data.
    str wzr, [x5]
    add x9, x5, #12
    adr x10, record_template
    mov x11, #136
1:
    ldr w12, [x10], #4
    str w12, [x9], #4
    subs x11, x11, #4
    b.ne 1b
    add x9, x5, #12
    mov x10, #1
    store64 10, x9, 40
    store64 20, x9, 48
    store64 21, x9, 56
    store64 22, x9, 64
    store64 23, x9, 72
    store64 24, x9, 80
    store64 25, x9, 88
    store64 26, x9, 96
    store64 27, x9, 104
    store64 28, x9, 112
    store64 27, x9, 120
    checksum x9
    add x10, x9, #136
    flush x9, x10, x11, x12, x13
    mov w10, #136
    stp w10, w10, [x5, #4]
    mov w10, #0x4244
    movk w10, #0x4347, lsl #16
    str w10, [x5]
    add x10, x5, #12
    flush x5, x10, x11, x12, x13

    mov x8, #TARGET_ADDRESS
    add x9, x8, #4096
    reject_overlap x8, x9, x27, x4
    reject_overlap x8, x9, x20, x7
    mov x10, x28
    mov x11, x8
    mov x12, #(stage2_end - stage2)
2:
    ldr w13, [x10], #4
    str w13, [x11], #4
    subs x12, x12, #4
    b.ne 2b
    flush x8, x11, x10, x12, x13
    ic iallu
    dsb sy
    isb
    br x8
blocked:
    wfe
    b blocked
.balign 16
record_template:
    .incbin "record.bin"
.balign 16
stage2:
    adr x19, stage2
    mov x5, #CONSOLE_ADDRESS
    add x9, x5, #12
    add x10, x9, #136
    mov x11, #136
3:
    ldr w12, [x9], #4
    str w12, [x10], #4
    subs x11, x11, #4
    b.ne 3b
    add x9, x5, #148
    mov x10, #2
    store64 10, x9, 40
    store64 19, x9, 120
    checksum x9
    add x10, x9, #136
    flush x9, x10, x11, x12, x13
    mov w10, #272
    stp w10, w10, [x5, #4]
    add x10, x5, #12
    flush x5, x10, x11, x12, x13
#if RESET_PSCI
    mov w0, #9
    movk w0, #0x8400, lsl #16
    mov x1, #0
    mov x2, #0
    mov x3, #0
reset_call:
    smc #0
#else
reset_call:
#endif
target_halt:
    wfe
    b target_halt
.balign 16
stage2_end:
.balign 4096
image_end:
