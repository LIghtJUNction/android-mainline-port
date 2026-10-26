/* SPDX-License-Identifier: MIT */
.section .text
.global _start
.global blocked
.global payload
_start:
    b entry
    .word 0
    .quad 0
    .quad PAYLOAD_SIZE + 4096
    .quad 0xa
    .quad 0, 0, 0
    .ascii "ARM\x64"
    .word 0

entry:
    adr x4, payload
    adr x12, _start
    adr x11, destination
    ldr w5, [x11]
    ldr w13, [x11, #4]
    orr x5, x5, x13, lsl #32
    adr x11, length
    ldr w6, [x11]
    ldr w13, [x11, #4]
    orr x6, x6, x13, lsl #32
    add x7, x5, x6
    cmp x12, x7
    b.hs copy
    cmp x4, x5
    b.ls copy
    b blocked

copy:
    cmp x4, x5
    b.eq clean
    cmp x5, x4
    b.lo forward
    add x7, x4, x6
    cmp x5, x7
    b.hs forward
    add x4, x4, x6
    add x5, x5, x6
backward:
    ldr w2, [x4, #-4]!
    str w2, [x5, #-4]!
    subs x6, x6, #4
    b.ne backward
    b clean
forward:
    ldr w2, [x4], #4
    str w2, [x5], #4
    subs x6, x6, #4
    b.ne forward

clean:
    adr x11, destination
    ldr w4, [x11]
    ldr w13, [x11, #4]
    orr x4, x4, x13, lsl #32
    adr x11, length
    ldr w6, [x11]
    ldr w13, [x11, #4]
    orr x6, x6, x13, lsl #32
    add x6, x4, x6
    mrs x9, ctr_el0
    ubfx x9, x9, #16, #4
    mov x10, #4
    lsl x10, x10, x9
clean_line:
    dc cvac, x4
    add x4, x4, x10
    cmp x4, x6
    b.lo clean_line
    dsb sy
    ic iallu
    dsb sy
    isb
    msr daifset, #0xf
    adr x11, device_tree
    ldr w0, [x11]
    ldr w13, [x11, #4]
    orr x0, x0, x13, lsl #32
    mov x1, xzr
    mov x2, xzr
    mov x3, xzr
    adr x11, destination
    ldr w4, [x11]
    ldr w13, [x11, #4]
    orr x4, x4, x13, lsl #32
    br x4
blocked:
    b blocked

.balign 8
destination: .quad LOAD_ADDRESS
device_tree: .quad DTB_ADDRESS
length: .quad PAYLOAD_SIZE
.org 4096
payload:
    .incbin PAYLOAD_PATH
