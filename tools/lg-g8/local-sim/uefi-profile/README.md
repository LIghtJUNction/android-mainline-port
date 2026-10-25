# 标准 ESP 构建 profile 补丁

`standard-esp.patch` 针对当前外部固件工作树中的 `Platforms/AndromedaPkg/Andromeda.dsc.inc`。
这里只保存补丁，没有改动那个带有未提交修改的固件工作树。

补丁应用后，在原来的底层 EDK2 `build` 命令上加：

```text
-D LG_ALPHA_STANDARD_ESP=TRUE
```

这个显式选项关闭 `LG_ALPHA_BDS_LINUXLOADER`、`LG_ALPHA_FORCE_UEFI_FASTBOOT` 和
`LG_ALPHA_FORCE_UEFI_FASTBOOT_EARLY`。不传此选项时，现有 USB 诊断 profile 的编译参数和预处理结果不变。
传 `FALSE` 会明确报错，避免把“已定义”误解为关闭选项。
补丁不新增 EFI loader，也不改变存储、USB、Secure Boot 或设备树驱动。

## 为什么同时关闭三个宏

当前源码调用链为：

1. `DeviceBootManagerLib.c:774` 在 BeforeConsole 调用 `MsBootOptionsLibRegisterDefaultBootOptions`。
2. `MsBootOptionsLib.c:533-535` 在 `LG_ALPHA_BDS_LINUXLOADER` 下把 LinuxLoader 注册到 BootOrder 的 0 位。
3. `LinuxLoader.c:197-205` 在 early 宏下，存储枚举之前就进入 fastboot；另一个 force 宏在 BoardInit 后仍会强制 fastboot。

标准 profile 不再注册这个实验性的首选项，保留原有 SDD、USB、PXE、FFU、启动菜单和内部 Shell 注册。
`Common/MU/PcBdsPkg/MsBootPolicy/MsBootPolicy.c:667-676` 的 SDD 路径查找非 USB 的 SimpleFileSystem，
`:367-387` 用文件系统设备路径调用 `EfiBootManagerBoot`。
`MU_BASECORE/MdeModulePkg/Library/UefiBootManagerLib/BmBoot.c:1099,1168` 将设备路径扩展为
标准 AArch64 的 `\EFI\BOOT\BOOTAA64.EFI`。这就是已有的 ESP 路由。
这恢复的是选择和查找路径；仍依赖真实 UFS/块设备驱动提供可读的 FAT/ESP 文件系统，补丁不验证这些硬件条件。

标准 Shell 已经被 `Platforms/AndromedaPkg/Frontpage.fdf.inc:67-70` 打入固件。
把同一份 Shell 放到 ESP 的 `EFI/BOOT/BOOTAA64.EFI`，同目录的 `startup.nsh` 可使用
`%homefilesystem%\kernel.efi` 调用 Linux EFI stub。Shell 的搜索顺序由
`MU_BASECORE/ShellPkg/Application/Shell/Shell.c:1227-1258` 确认：先自身目录，再 PATH。

这不保证任何现存的 EFI 变量会自动改变。`RegisterFvBootOption` 对已有条目只复用、不重排。
旧的 `BootOrder`、指向 LinuxLoader 的 `Boot####` 和一次性的 `BootNext` 仍可能影响选择。
源码启用 emulated non-volatile variables 也不能证明手机目前的变量值或持久化方式。
本补丁不删除、不重写任何变量；本机没有读取或验证手机上的这些变量。
`SecureBoot`/`SetupMode` 还会影响未签名 EFI 与 `dtb=` 的接受，需与 Android 解锁状态区分。

## 允许外部开发内核的已有构建目标

固件源码已有 `Platforms/SurfaceDuo1Pkg/SurfaceDuo1NoSb.dsc`。
它与普通 `SurfaceDuo1.dsc` 只有两处定义不同：`SECURE_BOOT=0` 和 `DEFAULT_KEYS=FALSE`。
开发构建应把原 EDK2 命令的 `-p` 目标换成这个 NoSb DSC，并增加标准 ESP 选项：

```text
-p Platforms/SurfaceDuo1Pkg/SurfaceDuo1NoSb.dsc -D LG_ALPHA_STANDARD_ESP=TRUE
```

这里的 `-p` 路径按固件源码根目录书写；使用配置了 PACKAGES_PATH 的包装器时，使用它对应的包内路径或该 DSC 的绝对路径。
保留原构建的 AARCH64、CLANGPDB、TARGET_DEVICE=lg-alpha 和其余必要参数。此处没有启动全量固件构建。

已通过实际 DSC parser 和源码预处理确认：

- `DEFAULT_KEYS=FALSE` 使 `ArmPlatformPkg/SecureBootDefaultKeys.fdf.inc:10` 的默认密钥 FFS 块不生成。
- `SECURE_BOOT=0` 使 `SecureBootProvisioningDxe.c:46-65` 不再无条件把安全启动视为开启，而是读取 PK 是否存在。
- 这**不会清除已存在的 PK**。`AuthVariableLib.c:175-179` 按 PK 是否存在设置 Setup/User mode；
  在 User mode 下，它会将被设成 0 的 SecureBootEnable 恢复为 1（`:219-223`），并据此生成 SecureBoot（`:255-258`）。

因此该已有开发目标适用于无已注册 PK 的变量环境；不能保证换一个 DSC 就关闭设备现有的安全启动。
本机 QEMU 可以在独立、干净的虚拟变量环境中验证未签名 EFI 和 `dtb=` 路径；手机现有变量仍未知。
标准 Shell 的只读 `dmpstore SecureBoot`、`dmpstore SetupMode`、`dmpstore PK` 可以提供将来的运行时证据，
但本项目的纯本机验证没有在手机上执行这些命令，也没有自动删除任何密钥或改 EFI 变量。
若运行时仍为 `SecureBoot=1`，应明确判为该开发路径条件未满足；Android bootloader 已解锁不能代替此条件。

## 旧 DefaultBDSBootApp 配置的边界

`Device/lg-alpha/Binaries/RawFiles/uefiplat.cfg:172` 确有 `DefaultBDSBootApp = "LinuxLoader"`。
但实际平台引用的是 `Device/lg-alpha/DXE.inc`，其中配置 RAW 文件段和 QcomBds 驱动已注释；
不能把 `Binaries/DXE.inc` 或 `Guid.xref` 中仍出现的名字当作当前固件实际包含它们的证据。

`audit_bds.py` 从实际 `SM8150_EFI.fd` 的 LZMA GUID section 解压 firmware volume，再逐个解析 FFS 头。
当前 v7 的 199 个文件包含标准 BdsDxe 和 LinuxLoader，不包含 QcomBds
（`5a50aa81-c3ae-4608-a0e3-41a2e69baf94`）或 uefiplat.cfg
（`dde58710-41cd-4306-dbfb-3fa90bb1d2dd`）文件，解压体也没有 `DefaultBDSBootApp` 字面值。
当前构建 FD 与归档 v7 FD 的 SHA-256 相同：
`4321229441d40515a9de7280cb16952d13065da07d316379cb3257befc54f035`。
这些证据排除了“当前 FD 直接打入了这两个 FFS 文件”的假设；它们不能证明外部 XBL、
继承的固件状态、其他 vendor 配置消费者或设备上的 EFI 变量没有影响启动。
标准 ESP profile 的 LG 实际启动可达性仍未验证。

```sh
python3 tools/lg-g8/local-sim/uefi-profile/audit_bds.py
```

该命令只读构建产物并将证据写入本目录 `out/bds-audit.json`，不会执行固件。

## 本机验证

```sh
python3 tools/lg-g8/local-sim/uefi-profile/validate.py \
  --source-root ../lg-g8-sources-20261025/mu_aloha_platforms
```

验证会先对外部工作树执行只读的 `git apply --check`，然后仅在输出目录的临时副本中应用补丁。
它使用该源码版本实际的 EDK2 `DscParser` 解析受影响的 BuildOptions 条件分支，
再让 Clang 预处理原始 LinuxLoader 和 MsBootOptions C 文件（只省略 include 行）。
默认 profile 的参数及预处理文本必须与补丁前相同；标准 profile 必须关闭三个宏，
保留正常磁盘和 Shell 注册；非法 `FALSE` 必须被真实 EDK2 parser 拒绝。
外部三个源文件的验证前后 SHA-256 必须一致。

输出 `out/report.json` 和预处理后的 `.i` 可审查各项证据。
这只验证构建条件和源代码分支，不是全量固件构建，也不是 LG 固件的 QEMU 整机运行证明。
