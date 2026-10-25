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
