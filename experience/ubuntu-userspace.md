# Ubuntu 用户态

推荐的整体架构和包拆分见 `../references/ubuntu.md`。这里只记具体的坑。写于 Ubuntu 26.04 + GNOME 49/50。

## 完成标准

- 原生 APT 能安装、升级、卸载所有项目包，dpkg 没有半配置的包。
- 全新安装：GNOME 首次设置向导缩放正确、触控可用；向导结束后 `systemctl --failed` 为空。
- 冷启动后没有因等待设备而超时的服务。

## 坑

### flash-kernel 报 Unsupported platform，dracut 停在 half-configured
标记：【高频】
- 现象：安装内核或更新 initramfs 时报错，`dpkg --configure -a` 失败。
- 原因：Debian 系的启动部署工具不认识自己管启动镜像的设备。
- 怎么解决：`/etc/flash-kernel/machine` 写 `none`（放进设备支持包），由项目自己的钩子生成启动镜像。
- 提前预防：第一次装包之前就关掉发行版的启动部署工具。
- 适用范围：Ubuntu/Debian ARM64；2026。

### 内核钩子缺前置文件时报错，导致 dpkg 半配置
- 现象：用户在首次设置向导里选键盘布局后，系统重建 initramfs，项目钩子因为缺少某个映射文件报错，dracut 处于 half-configured。
- 原因：钩子假设所有文件都已就位；任何包更新都可能触发 initramfs 重建。
- 怎么解决：钩子在前置文件缺失时打印提示并退出 0，不写槽、不删镜像集；安装步骤的顺序以"任何时刻重建 initramfs 都无害"为准。
- 适用范围：Ubuntu；2026。

### dracut 在构建机上生成 initramfs，带上的是构建机的硬件
- 原因：`hostonly=yes` 时 dracut 按当前运行环境（QEMU、chroot）挑模块。
- 怎么解决：`hostonly="no"`、`hostonly_cmdline="no"`，显式列出需要的模块和文件系统。代价是 initramfs 带上全部模块，生成慢、体积大，要核对启动分区余量。
- 适用范围：dracut；2026。

### 通用内核被装回来
- 现象：`apt upgrade` 后系统里出现发行版通用内核，启动镜像钩子被干扰。
- 原因：官方镜像带 `linux-generic`、HWE 元包、headers、`kdump-tools`。
- 怎么解决：构建时 purge 这些包；顺序是先装发行版更新和项目用户态包，再 purge 通用内核，最后装项目内核，这样 initramfs 只生成一次。
- 适用范围：Ubuntu；2026。

### 内核版本名复用，旧模块被新内核加载
- 现象：同一个版本名对应两份不同的构建；`linux-version sort` 把带 `+` 的版本排错；调试用的 RAM 镜像与已安装的模块目录不匹配。
- 怎么解决：每次打包都升一个 ABI 号（`<版本>-<项目><N>`），从不复用；不用 `+` 后缀；维护一个 ABI 账本（版本名、内核提交、配置哈希）。
- 适用范围：通用；2026。

### APT 自动删除了项目内核包
- 原因：APT 只保护名字以 `linux` 等前缀开头的内核包。
- 怎么解决：项目内核包遵循 `linux-image-<release>` 命名，或在 `APT::NeverAutoRemove` 里加精确包名。
- 适用范围：Ubuntu；2026。

### snap 应用 GPU 不工作，或接上自建 GPU 库后应用打不开
标记：【耗时】
- 现象一：Firefox、Thunderbird、应用商店等 snap 应用是软件渲染。原因：严格沙箱 snap 的 OpenGL 用户态来自 `gpu-2404` 接口的提供者 `mesa-2404`，里面的 Mesa 可能不认识新 GPU；宿主机自编的 Mesa 进不了沙箱。
- 现象二：把这些 snap 连到自建的 GPU 提供者后，Thunderbird、应用商店直接打不开（缺 `libXfixes` 等）。原因：这些 snap 打包时删掉了 X11、libxml2 等库，指望 `mesa-2404` 补回来；自建提供者只带了 Mesa。
- 怎么解决：自建提供者以原版 `mesa-2404` 的完整内容为底，只替换 Mesa 库；用消费者 snap 的实际依赖做加载检查；首启服务自动安装提供者并连接所有带 `gpu-2404` 插头的 snap。应用迁到 core26 后接口变成 `gpu-2604`，要再出一个。
- 提前预防：离线验证不能只查提供者自己的依赖，要模拟消费者加载。
- 适用范围：Ubuntu snap；2026。

### 想从镜像里删掉 Firefox snap，删 seed 条目不行
- 原因：官方 Desktop 镜像的 snap 已经预置（preseed），`state.json` 里登记了待完成的任务；只删 seed 条目会让任务找不到文件；在构建虚拟机里 `snap remove` 会因 snapd 超时整体回滚。
- 怎么解决：镜像里装 Mozilla 官方 APT 源的 deb 版 Firefox（加源、密钥、pin）；首启后由一次性服务在 seeding 完成后执行 `snap remove --purge firefox`，并确认 deb 版已存在才动手；收藏栏改指向 deb 版。
- 适用范围：Ubuntu 22.04 之后；2026。

### snap 挂载失败或 snap-confine 缺能力
- 原因：内核缺 SquashFS XZ/LZO/xattr；普通用户解包 squashfs 丢扩展属性和设备节点；`security.capability` 丢失。
- 怎么解决：补内核配置；用 root 和保留 xattr 的工具解包；在真实文件上检查能力位。
- 适用范围：Ubuntu；2026。

### 在 QEMU 里测试 snap 行为的结论不能外推到真机
- 原因：TCG 下 snapd 的操作经常超时并回滚事务。
- 怎么解决：snap 相关验收留到真机；AppArmor/snapd 类问题设时间上限。
- 适用范围：通用。

### GNOME 首次设置向导的缩放是 100%，字很小
- 原因：向导用临时账户运行，没有持久配置目录，放在用户家目录里的显示配置读不到；借用固件画面时没有物理尺寸，Mutter 自动选 100%。
- 怎么解决：系统级配置 `/etc/xdg/monitors.xml`，GDM、向导、用户会话都会读；根本解决是让显示驱动提供物理尺寸。
- 适用范围：GNOME；2026。

### 首次设置向导写入非法键盘布局，`console-setup` 失败
- 现象：向导之后 `console-setup.service` 失败，系统处于 degraded。
- 原因：向导写入 `XKBLAYOUT="default,us"`。
- 怎么解决：drop-in 在 `console-setup` 之前清理非法值。
- 适用范围：Ubuntu 26.04；2026。

### 首次设置向导覆盖主机名
- 原因：向导总是把用户名写进计算机名。
- 怎么解决：设置 pretty hostname（蓝牙名用它）；文档提醒用户在向导里修改。
- 适用范围：GNOME；2026。

### 系统语言仍是英文
- 原因：向导只设置用户语言。
- 怎么解决：需要时设置 `/etc/default/locale` 并预装语言包。
- 适用范围：Ubuntu；2026。

### 电源键行为不符合平板习惯
- 现象：短按弹出关机对话框，60 秒后自动关机；或默认挂起而挂起还没实现。
- 怎么解决：挂起未完成时，GSettings `power-button-action='nothing'` 或保留对话框（由用户决定），logind `HandlePowerKey=ignore`；挂起完成后改为"按键熄屏锁屏"。
- 适用范围：GNOME；2026。

### 服务里访问不到用户 D-Bus
- 原因：`ProtectHome=yes` 把 `/run/user` 也遮住了。
- 怎么解决：`ProtectHome=read-only` 并单独放开 `/run/user`。
- 适用范围：systemd。

### socket 绑定到开机时还不存在的网卡，开机卡 90 秒
标记：【致命】
- 现象：开机慢 90 秒，SSH 不监听，显示管理器也被拖慢。
- 原因：调试 SSH 的 socket 用了 `BindToDevice=usb0`，而 `usb0` 要等调试服务创建，服务又等 socket 所在的启动目标，互相等待到超时。运行中切换测试永远不会触发。
- 怎么解决：绑定固定地址并加 `FreeBind=yes`；用 `IPAddressAllow`/`IPAddressDeny` 限制只接受 USB 网段。
- 提前预防：改 unit、socket、udev、initramfs、生成器后必须冷启动验证，并先用旧版本复现问题做对照。
- 适用范围：systemd；2026。

### zram 生成器在没有 zram 的内核上让开机多等 90 秒
- 怎么解决：内核开启 zram 后再装生成器配置，或构建时检查。
- 适用范围：Ubuntu；2026。

### 升级路径下服务没有被重新启用
- 原因：包迁移时删掉了 enabled 链接，`deb-systemd-helper enable` 把它当成"用户已禁用"，不再重建。
- 怎么解决：用设备上同版本的原包工具离线复现真实升级；改调试通道相关的包之前确认有回退路线。
- 适用范围：Debian 打包；2026。

### 镜像里残留构建机的 apt 列表，用户第一次 `apt install` 失败
- 怎么解决：构建结束时清理 `/var/lib/apt/lists`；文档写"先 `sudo apt update`"。
- 适用范围：通用。

### 用户误以为 home 只有 60 GB
- 原因：GNOME"关于"页只显示根分区。
- 怎么解决：文档说明。
- 适用范围：GNOME；2026。

### `/usr/local/sbin` 里的旧脚本遮住了包里的新版本
- 怎么解决：不要在设备上手放脚本；全部走包。
- 适用范围：通用。

### 手工拼装的根文件系统缺文件
- 现象：触控固件目录缺失；dconf profile 缺失导致缩放、屏幕键盘、空闲策略全失效；mdev 把 `/dev/null` 权限改成 0660 拖垮 D-Bus、logind、GDM。
- 怎么解决：设备层作为构建产物（软件包），带清单和安装自检；配置要验证到实际读取它的程序。
- 适用范围：通用。

### GDM 会话读的 dconf 不是你以为的那份
- 原因：GDM 会话的 `XDG_CONFIG_HOME` 指向 `/var/lib/gdm3/seat0/config`。
- 怎么解决：读 GSettings 时复制目标进程的环境。
- 适用范围：GNOME；2026。

### 锁屏时屏幕键盘空指针
- 怎么解决：用 GNOME Shell 扩展加守卫；没有物理键盘时测试一次。
- 适用范围：GNOME 49；2026。

### NTFS3 文件名乱码
- 原因：默认字符集是 ISO-8859-1，同一驱动读写往返看不出问题。
- 怎么解决：挂载参数指定 `iocharset=utf8`。
- 适用范围：通用。

### 锁屏后十几秒按键没有反应
- 现象：按电源键锁屏挂起，偶尔要等约 30 秒才真正挂起，期间按键被忽略。
- 原因：gnome-shell 持有的睡眠延迟锁偶发不释放，logind 等到 `InhibitDelayMaxSec` 才强制挂起；这个上限被 unattended-upgrades 的配置设为 30 秒。具体是哪个锁残留未定位。
- 怎么确认：被动采集 `ListInhibitors` 和 Shell 主线程等待点；睡眠时长按墙上时间看。
- 怎么解决：定位残留锁后在上游或发行版层修；不要靠调低 `InhibitDelayMaxSec` 掩盖。
- 适用范围：GNOME 50、Ubuntu 26.04；2026。

### 应用商店中文全是方块
- 原因：镜像自带的 snap-store 版本里，ARM64 Flutter 引擎没有系统字体回退，上游已修。
- 怎么解决：`snap refresh snap-store` 到修复版本。
- 提前预防：遇到发行版自带应用的问题，先查上游是否已在新版本修复，再考虑自己改。
- 适用范围：Ubuntu 26.04 ARM64；2026。

### 包依赖把基础功能和某个桌面版本绑死
- 现象：设备支持包依赖显示电源包，后者硬依赖特定版本的 gnome-settings-daemon，导致双系统切换、无线地址这些基础功能也离不开 GNOME。
- 怎么解决：基础设备包不依赖桌面组件；桌面适配单独成包。
- 适用范围：打包设计；2026。

### `Type=oneshot` 服务在启动过程中反复运行，撞上启动次数限制
- 现象：冷启动验收报某个服务失败；该服务一次启动跑了 4–6 次。
- 原因：服务挂在 sysinit.target 下但没有 `RemainAfterExit=yes`，之后每次有服务拉起 sysinit 的 Wants 就重跑一次，最终触发 `StartLimitBurst`。例：Ubuntu 的 `fake-hwclock-load.service`（每次运行还会把时钟拨回保存值）。
- 怎么解决：drop-in 加 `RemainAfterExit=yes`。
- 提前预防：验收报失败服务时，先数它在多次启动里的 "Starting" 次数，再决定是不是本次改动引起的。
- 适用范围：systemd；2026。

### 修 GNOME 的行为时不要 fork 核心组件
- 做法：Mutter、gnome-shell、gnome-settings-daemon、GTK、GStreamer 等核心包不 fork；确实需要时基于发行版源码包打最小补丁（版本加 `+<项目>1`），固定来源并登记退出条件（上游合并后撤掉）。
- 适用范围：Ubuntu；2026。
