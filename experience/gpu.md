# GPU 与图形栈

## 推荐路线

1. **内核**：上游 `drm/msm` 对该代 Adreno 的支持（GPU 时钟控制器、GMU、SMMU、OPP 表）。新一代 GPU 常先出现在 linux-next、高通在审系列或社区内核，按来源导入，上游合入后换成正式版本。
2. **固件**：sqe、GMU、zap shader。零售机的安全世界通常拒绝通用签名的 zap，只能用本机原厂签名的文件；有的平台可以先走免 zap 路径验证绘制。
3. **用户态**：先确认发行版 Mesa 是否认识这颗 GPU（看完整 chip id，不只看系列号）。不认识就自编 Mesa，装在独立前缀，只在 GPU 驱动已加载时通过环境变量启用，卸载即回到发行版版本。
4. **沙箱应用**：snap 和 flatpak 用自己的 GL 库，见 `ubuntu-userspace.md`。
5. **新 GPU 的 Mesa 不成熟**：视频纹理、多上下文、Vulkan 路径最容易出问题。跟踪上游 stable 和 main 的修复，按提交回移，并把每个回移登记为技术债，上游发版后换回。

## 完成标准

- 渲染器名称是硬件 GPU（不是 llvmpipe/softpipe），渲染结果逐像素与预期一致。
- 多轮运行时电源切换后仍能渲染；较长时间（例如 200 秒以上）没有 GMU 错误和 hangcheck。
- 桌面合成器实际使用 GPU，帧率接近刷新率。
- 浏览器、视频播放器、设置等复杂应用画面正确，不崩溃；GTK 的 GL 和 Vulkan 渲染器都要测。

## 坑

### 发行版 Mesa 不认识新 GPU，桌面退回软件渲染
- 现象：点击、打字明显延迟；渲染器是 llvmpipe。
- 原因：Mesa 设备表只按完整 chip id 匹配（例如有 `0x44050000` 但本机是 `0x44050001`）。
- 怎么解决：自编 Mesa 并加上单行设备表修复（上游通常很快有）；核对完整 chip id；注意别把 APT 候选版本当成已安装版本。
- 适用范围：Adreno 8xx；2026。

### GMU 或 GPU 超时，其实是显示资源被回收引起的
- 现象：GMU HFI 超时、hangcheck，与 GPU 代码无关。
- 原因：借用固件画面时显示总线时钟被回收，内存通路降速（见 `kernel-and-resources.md`）；或 MX 依赖票查错 OPP 表、电源域投到 retention 档。
- 怎么解决：先排除显示侧资源问题，再查 GPU 电源投票。
- 适用范围：高通；2026。

### 多个 GL 上下文同时创建时崩溃（共享上下文竞态）
标记：【耗时】
- 现象：设置里有多个视频演示的页面一打开就崩，`SIGSEGV in _mesa_release_pending_resource` ← `delete_buffers`。
- 原因：Mesa 通用 GL 前端在新上下文的状态跟踪器就绪前就把它挂进共享列表；threaded context 下遍历共享列表时读到空指针。freedreno 总是启用 threaded context，所以必现；发行版 Mesa 走软件渲染反而不复现。
- 怎么确认：core 里找到正在创建的上下文；写一个并发创建、销毁上下文的压力用例，原版崩、修正版不崩。
- 怎么解决：新上下文就绪后再入列；向上游提交最小修正。`GALLIUM_THREAD=0` 能绕过但有性能代价。
- 适用范围：Mesa 26.x 及之前，GL 前端通用问题；2026。

### GTK Vulkan 渲染器下 NV12 视频和相机预览出现斜纹
标记：【耗时】
- 现象：GTK 应用里的 NV12 视频、相机预览有斜条纹；GL 渲染器正常。
- 原因：Turnip 在 A8xx 上 buffer→image 上传的行距单位错误（上游 stable 已修，自编版本缺这个提交）。
- 怎么确认：无窗口复现器跑全部模式；临时修改行距系数做正反因果对照。
- 怎么解决：回移上游修复；同时检查 snap GPU 提供者是否也更新。修好之前相机可以临时输出 BGRx 绕过（CPU 开销明显，NEON 转换可以减半），修好后退回 NV12。
- 适用范围：Mesa Turnip A8xx；2026。

### 视频播放器播放时 Mesa 断言退出
- 现象：`fd6_texture.cc ... [CHIP = A8XX] ... Assertion` 后 SIGABRT。
- 原因：新 GPU 的某些纹理格式路径还不完整。
- 怎么解决：查上游是否已有修复；没有时向上游报告，并在已知问题里写明受影响的应用。
- 适用范围：freedreno A8xx；2026。

### 某个代际的 GMEM 基址返回值错误，浏览器和 Electron 应用花屏
- 原因：内核对某代 GPU 跳过了 GMEM 范围设置却返回了错误的基址。
- 怎么确认：把真实应用调用缩成原生 GLES 最小用例；用驱动调试开关（例如 `FD_MESA_DEBUG=sysmem`）对照；用 LD_PRELOAD 拦截参数查询做单变量。
- 怎么解决：内核一行修复（上游已有）；修复后撤掉应用层的绕过参数。
- 适用范围：Adreno 7xx；2026。

### Vulkan 在新 GPU 上挂起
- 现象：`vkcube` 触发 `hangcheck detected gpu lockup`。
- 怎么解决：首版不默认启用 Vulkan 应用；GTK 可以先用 GL 渲染器。
- 适用范围：Turnip 新代际；2026。

### 在 QEMU 或软件渲染下"通过"
- 原因：Mesa 自动退回 softpipe/llvmpipe，像素检查照样通过。
- 怎么解决：测试断言渲染器名称是硬件 GPU，并准备一个要求硬件 GPU 的负例。
- 适用范围：通用。

### 帧测试拿不到画面
- 原因：GDM 登录界面不出应用帧；线性抓帧工具拒绝高通压缩格式。
- 怎么解决：帧测试用无头 GNOME（`gnome-shell --headless --virtual-monitor`）；不要拿光标平面冒充主画面。
- 适用范围：GNOME；2026。

## 未解决

- 新 GPU 的 Vulkan 稳定性、长时间负载、调频和电源管理。
