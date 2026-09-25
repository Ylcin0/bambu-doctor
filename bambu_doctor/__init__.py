"""bambu-doctor：Bambu Lab 用户的 profile 体检工具。

读取你本机 Bambu Studio 的配置文件，解析 profile 的继承链，找出参数隐患，
并生成「实测值 vs 官方基线」对照表。

设计原则：
- 纯标准库，零第三方依赖
- 不打包任何 Bambu 官方预设数据（版权归 Bambu Lab），一切从用户本机读取
- 不上传任何东西，纯本地分析
"""

__version__ = "0.1.0"

__all__ = ["__version__"]
