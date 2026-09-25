# bambu-doctor

[![tests](https://github.com/Ylcin0/bambu-doctor/actions/workflows/tests.yml/badge.svg)](https://github.com/Ylcin0/bambu-doctor/actions/workflows/tests.yml)

**体检你的 Bambu Studio profile** —— 解析 profile 的继承链，找出藏在里面的参数隐患，并生成「你的值 vs 官方基线」调参对照表。

> 📐 **项目方向见 [DESIGN.md](DESIGN.md)**：目标是从「参数体检」扩展成「**打印问题顾问**」——拍张照片，它告诉你哪里出了问题、该改哪个参数。
>
> ⚠️ **当前版本只是它的地基**（参数真值解析 + 体检）。DESIGN 里规划的 AI 诊断部分**尚未实现**，本版本完全离线、不调用任何 AI 服务。

纯本地分析，不联网、不上传任何数据、零第三方依赖。

> 🛠 **这个项目是 vibe coding 出来的**
>
> 代码由 AI 写，人负责定需求、做架构决策、在**真实的 Bambu Lab A1 上验证结果**。
>
> 这么标注不是为了免责 —— 恰恰相反：文档里所有实测数字（继承链深度、温度偏差、第三方耗材预设的子目录结构）都是在真机上跑出来的，不是编的；55 个自动化测试兜住核心逻辑，改动不会悄悄破坏已有行为；每条体检规则都对应一个真实踩过的坑。**发现不对，提 Issue 就是最好的贡献方式。**

```
$ bambu-doctor
WARNING R2 [Bambu PETG White]
        跨机型继承：继承的是「X1C」的预设，你用的是「A1」
        → 考虑改成继承「A1」对应的预设，或显式覆盖掉差异项。
WARNING R3 [Bambu PETG White]
        喷嘴温度 255℃ 比本机型官方值 245℃ 高 10℃
        → 要贴近本机型的话，把喷嘴温度调到 245℃ 左右再逐次试。
INFO    R4 [深蓝]
        回抽长度未设，实际用的是机器级的 1.0mm
        → 若这是有意的就忽略；否则在这个耗材档里显式设一个回抽长度。

合计：0 严重 / 5 警告 / 2 提示
```

## 为什么需要它

Bambu Studio 的 profile 是**覆盖式**的：一个 profile 只保存与它继承的父级**不同**的键。真实参数值散落在整条继承链上：

```
我的PETG ← Bambu PETG Basic @BBL A1 ← Bambu PETG Basic @base
         ← fdm_filament_pet ← fdm_filament_common
```

实测 Bambu Studio 自带的 `0.20mm Standard @BBL A1` 只有 **11 个键**，`Bambu PETG Basic @BBL A1` 只有 **21 个键**——剩下上百个参数值都在父级里。

于是出现了几个很难自己发现的问题：

- **跨机型继承**：从别人（或别的机器）那拷来的 profile，继承的是 X1C 的预设。X1C 是封闭机箱、有辅助风扇，A1 是开放机、`auxiliary_fan=0`，官方给两者的 PETG 温度差 10℃。
- **参数落空**：耗材档里回抽长度是 `nil`，于是静默回落到**机器级**的回抽值。你以为用的是耗材档里的设定，其实不是。
- **温度随机型错位**：换了打印机没换 profile，温度还是老机器的。

这些在切片器界面上都看不出来。bambu-doctor 把继承链合并平，然后逐条检查。

## 安装

```bash
pip install .
```

或直接跑，不用安装：

```bash
python -m bambu_doctor
```

需要 Python 3.9+，无第三方依赖。

## 用法

```bash
bambu-doctor                  # 体检（默认命令，等同于 check）
bambu-doctor check            # 同上
bambu-doctor check --strict   # 有 warning 就返回退出码 1（可进 CI）
bambu-doctor check --json     # 机器可读输出
bambu-doctor check -o report.md   # 写成 markdown 报告
bambu-doctor extract          # 生成 profile 档案（5 份 markdown）
bambu-doctor report           # 打印调参真值表
bambu-doctor export -o out.json   # 导出完整结构化数据
```

Bambu Studio 配置目录默认按平台自动探测（Windows `%APPDATA%\BambuStudio`、macOS `~/Library/Application Support/BambuStudio`、Linux `~/.config/BambuStudio`）。装在别处就手动指定：

```bash
bambu-doctor --studio-dir /path/to/BambuStudio
```

### `extract` 生成的五份档案

| 文件 | 内容 |
|---|---|
| `machine.md` | 机器参数（逐项标注来自继承链的哪一层） |
| `filament.md` | 每个耗材 profile 的全部参数 + 来源层，`← 你调的` 标出你改过的键 |
| `process.md` | 工艺 profile 同上 |
| `tuning-reference.md` | **调参直接查这张**：你的值 vs 官方基线 |
| `gcode.md` | 被改写的开始/结束 G-code 全文 + 与官方模板的行级差异 |

档案里每个参数都标了**来自哪一层**——调参时最常搞错的就是"我明明在耗材档里改了啊"，其实改的那层根本不是生效的那层。

## 体检规则

| 规则 | 检查什么 | 级别 |
|---|---|---|
| **R1** | 继承链断裂——父级在系统里不存在（从别的机器/插件拷来的 profile） | warning |
| **R2** | 跨机型继承——继承的是别的机型的预设 | warning |
| **R3** | 喷嘴温度偏离本机型官方值超过 5℃ | warning |
| **R4** | 耗材参数为 `nil` → 静默回落到机器级设置 | info |
| **R5** | 工艺/机器档参数几乎相同（同一套参数的两次尝试） | info |
| **R6** | 继承链深度与各层贡献（`--verbose` 才显示） | info |

退出码：有 warning 时 `--strict` 返回 1；默认只对 error（目前没有规则产生 error）返回 1。

### 规则豁免

体检规则是**提醒**，不是判决。有些偏离是你有意为之的（比如为了压拉丝主动降温 15℃），每次跑都报同一批已知项，人就不看报告了。用配置文件声明"这条我知道"：

```toml
# .bambu-doctor.toml　放在当前目录或用户主目录即自动生效
[check]
disable = ["R5"]              # 全局关闭某条规则

[[ignore]]                    # 针对特定 profile 豁免
profile = "SUNLU PETG"        # 子串匹配
rules = ["R3"]
reason = "有意降温压拉丝"      # 豁免理由会出现在输出里
```

```bash
bambu-doctor --config my.toml     # 指定配置文件
bambu-doctor check --show-ignored # 列出被豁免的发现
```

查找顺序：`--config` → 当前目录 → 用户主目录。完整示例见 `examples/bambu-doctor.toml`。

两点设计上的克制：**一条空配置不会吞掉所有发现**（避免危险的静默失败）；被豁免的数量**总是会报出来**，不会悄悄消失。

## 数据来源

全部从你本机读取：

- `<配置目录>/user/<账号ID>/{machine,filament,process}/*.json` —— 你的自定义 profile
- `<配置目录>/system/BBL/{machine,filament,process}/**/*.json` —— 官方预设，用作对照基线

**本仓库不包含任何 Bambu Lab 的官方预设数据**（版权归 Bambu Lab）。所有基线都在运行时从你本机安装的 Bambu Studio 读取。

第三方耗材预设按厂商放在子目录里（`system/BBL/filament/SUNLU/`、`Polymaker/` 等），扫描时会递归处理。

## 已知限制

- 只支持 Bambu Studio 的配置格式。Orca Slicer 用的是同一套 profile 格式，理论上可用（`--studio-dir` 指到 Orca 配置目录即可），但未测试。
- R3 的对照基线是**本机型同料类型的官方基础预设**，不是"绝对正确的温度"。差得远才说明有问题，差得少属于正常调参。
- R5 只查工艺/机器档。按颜色建多个耗材 profile 是正常用法，参数接近不算重复。
- 不做 G-code 语义分析，只报告改写点与官方模板的差异。

## 开发

```bash
python -m unittest discover -s tests -t . -v
```

测试用构造出来的假 profile 数据，不需要装 Bambu Studio。

## 这个项目是怎么写出来的

**vibe coding**：人描述需求 → AI 写实现 → 人到真机上验证 → 有问题再描述给 AI 改。

| 谁 | 做什么 |
|---|---|
| **人** | 定需求、定架构、在真实打印机上验证、判断输出对不对 |
| **AI** | 写实现、写测试、把验证跑通 |

值得一说的是：这个流程里**人的活没有变少，只是换了位置**——从"写代码"变成"定义问题和验收结果"。本文档里那些具体数字（继承链 4~5 层、255℃ vs 245℃ 的偏差、`filament/SUNLU/` 这种第三方预设的子目录布局）全都来自真机实测，因为 AI 编不出这些。

## 许可

MIT
