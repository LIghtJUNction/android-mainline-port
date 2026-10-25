# UEFI fastboot 的本机控制流验证

从仓库根目录运行：

```sh
python3 tools/lg-g8/local-sim/fastboot/run.py \
  --source-root ../lg-g8-sources-20261025/mu_aloha_platforms \
  --out out/local-sim/fastboot
```

需要 Python 3 和 Clang。无需连接手机。

脚本从指定源码复制完整的 `FastbootMain.c`、`UsbDescriptors.c`，只替换 include；
从 `FastbootCmds.c` 提取 13 个函数的原文。manifest 记录每个源码和函数的 SHA-256。
它们在本机 x86_64 上使用 ASan/UBSan 执行，EFI、USB、存储和板级操作由明确的边界替身提供。
这是 C 控制流验证，不是 ARM64 固件执行，也不验证真实 Qualcomm 协议实现。

31 项检查包括正常路径、资源不足、错误返回、传输事件，以及 4 个有界挂起复现。
部分 PASS 表示**成功复现预期的错误或 sanitizer 报错**，不能理解为这些功能正常。
完整判定条件在 `run.py` 的 `CASES` 中，单项原始日志在输出目录中。

本次源码可复现的现象：

- 下载缓冲区分配和命令设置发生在 `StartEx` 前；它们自己就可能失败或挂住。
- `FastbootCommandSetup` 返回错误后，调用方仍继续进入 USB 初始化。
- 描述符构造中串号读取或内存分配失败，会留下未初始化输出；调用方继续使用时被 sanitizer 捕获。
- 如果边界实现保留描述符指针，局部指针变量的生命周期存在问题。这一项依赖替身的保留行为，不能据此断言手机固件确实如此。
- 清理路径先 Stop，再经相同协议释放部分缓冲区；严格生命周期替身会拒绝该操作。

这些结果缩小软件审计范围，但不能把手机黑屏定位到某一个 EFI 调用。

## RAM 诊断命令表边界（离线）

`audit_commands.py` 检查**已经按实际构建参数预处理、单独提取**的
`struct FastbootCmdDesc cmd_list[] = {...};` 声明：

```sh
python3 tools/lg-g8/local-sim/fastboot/audit_commands.py /path/to/effective-command-table.c
python3 -m unittest discover -s tools/lg-g8/local-sim/fastboot -p 'test_*.py' -v
```

路线与验收：限制这一张表的命令入口，以合成 fixture 验证允许表、危险命令、未知命令、
错误 handler、重复项、缺失 sentinel、未预处理条件和无效语法。脚本不调用设备工具，
不编译固件、不生成镜像；JSON 记录输入字节 SHA-256，拒绝时退出码为 1。
CI 只运行这些合成测试，无需外部固件、私人材料或设备。

白名单只有 `getvar:`/`CmdGetVar`、`download:`/`CmdDownload` 和 `boot`/`CmdBoot`。
其他入口（包括 OEM 设置、重启、分区写入、槽切换及解锁）默认拒绝。
输入应保留首项 `{"", NULL}`：当前注册循环从索引 1 开始。
可接受这些命令的子集，但空表拒绝。未经预处理的 `#ifdef` 会拒绝，不能通过
扫描源文件中的宏名推断最终配置。源码依据为现有模拟器使用的
`Platforms/QcomModulePkg/Library/FastbootLib/FastbootCmds.c` 中
`FastbootCommandSetup` 的 `cmd_list` 与注册循环；上游默认表中的 OEM 命令也会被拒绝。

**PASS 只表示该输入声明符合命令入口限制**，不是固件 RAM-only 认证，也不是启动结果。
调用者必须保存完整预处理输出、构建参数、固件仓源码 SHA 和提取依据，核对该声明
确实是被编译和注册的表，并单独审计其他注册点及 handler 的传递调用和副作用。
同名 handler 可以包含存储写入；本工具无法识别。不能用删掉不想看的行的表证明固件安全。
尤其不能据此断言 `CmdBoot` 本身没有持久化副作用。

2026-10-25：本轮仅离线合成测试。公开仓基线为
`547e2ed7d7e4e0ebaa528c30b87c62ffb8013ac7`；外部固件缺少可信 Git base SHA，
不能把公开仓 SHA 当作固件基线。没有改变 LG G8 的真机状态，也没有形成新的刷写候选。
下一步唯一目标是取得可核验的固件源码 SHA 与完整预处理构建输入，再审计有效表及 handler；
硬件启动和 USB 阶段归因仍需独立证据。设备未连接、未操作。
