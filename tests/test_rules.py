"""规则豁免配置的测试。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from bambu_doctor.checks import Finding
from bambu_doctor.rules import (
    ConfigError,
    RuleConfig,
    apply_ignore,
    find_config,
    load_config,
)


def write_config(directory: str, text: str, name: str = ".bambu-doctor.toml") -> Path:
    path = Path(directory) / name
    path.write_text(text, encoding="utf-8")
    return path


def make(rule: str, profile: str = "") -> Finding:
    return Finding(rule=rule, level="warning", title="t", detail="d", profile=profile)


class TestFindConfig(unittest.TestCase):
    def test_explicit_path_missing_raises(self):
        with self.assertRaises(ConfigError):
            find_config("D:/definitely/not/here.toml")

    def test_explicit_path_used(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_config(tmp, "[check]\ndisable = []\n")
            self.assertEqual(find_config(path), path)

    def test_named_config_found_in_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_config(tmp, "[check]\n")
            self.assertEqual(load_config(path).source, path)


class TestDisable(unittest.TestCase):
    def test_globally_disabled_rule_is_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_config(tmp, '[check]\ndisable = ["R5"]\n')
            config = load_config(path)
            self.assertTrue(config.is_ignored(make("R5")))
            self.assertFalse(config.is_ignored(make("R3")))

    def test_single_string_instead_of_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_config(tmp, '[check]\ndisable = "r3"\n')
            config = load_config(path)
            # 大小写不敏感
            self.assertTrue(config.is_ignored(make("R3")))


class TestPerProfileIgnore(unittest.TestCase):
    def test_profile_and_rules_both_must_match(self):
        config = RuleConfig(ignore=[{"profile": "悬挂参考", "rules": ["R3"], "reason": "有意"}])
        self.assertEqual(config.is_ignored(make("R3", "悬挂参考PETG")), "有意")
        self.assertEqual(config.is_ignored(make("R2", "悬挂参考PETG")), "")
        self.assertEqual(config.is_ignored(make("R3", "别的profile")), "")

    def test_profile_is_substring_match(self):
        config = RuleConfig(ignore=[{"profile": "深蓝", "rules": ["R4"]}])
        self.assertTrue(config.is_ignored(make("R4", "深蓝")))

    def test_rules_only_applies_to_all_profiles(self):
        config = RuleConfig(ignore=[{"rules": ["R6"]}])
        self.assertTrue(config.is_ignored(make("R6", "任意profile")))

    def test_profile_only_ignores_all_rules_of_that_profile(self):
        config = RuleConfig(ignore=[{"profile": "优化"}])
        self.assertTrue(config.is_ignored(make("R2", "优化")))

    def test_empty_entry_does_not_swallow_everything(self):
        """一条空配置不能把所有发现都吃掉——那是危险的静默失败。"""
        config = RuleConfig(ignore=[{}])
        self.assertFalse(config.is_ignored(make("R3", "任意")))

    def test_r5_combined_profile_field(self):
        """R5 的 profile 字段形如 "A / B"，两边都该能命中。"""
        config = RuleConfig(ignore=[{"profile": "优化设置", "rules": ["R5"]}])
        self.assertTrue(config.is_ignored(make("R5", "优化 / 优化设置")))


class TestApplyIgnore(unittest.TestCase):
    def test_splits_kept_and_ignored(self):
        config = RuleConfig(disable={"R5"})
        findings = [make("R3", "a"), make("R5", "b"), make("R2", "c")]
        kept, ignored = apply_ignore(findings, config)
        self.assertEqual([f.rule for f in kept], ["R3", "R2"])
        self.assertEqual([f.rule for f in ignored[0][0:1]][0], "R5")
        self.assertEqual(len(ignored), 1)

    def test_empty_config_is_passthrough(self):
        findings = [make("R3"), make("R5")]
        kept, ignored = apply_ignore(findings, RuleConfig())
        self.assertEqual(kept, findings)
        self.assertEqual(ignored, [])

    def test_reason_is_reported(self):
        config = RuleConfig(ignore=[{"profile": "a", "rules": ["R3"], "reason": "有意为之"}])
        _, ignored = apply_ignore([make("R3", "a")], config)
        self.assertEqual(ignored[0][1], "有意为之")


class TestConfigErrors(unittest.TestCase):
    def test_malformed_toml_raises_config_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_config(tmp, "[check\ndisable =\n")
            with self.assertRaises(ConfigError):
                load_config(path)

    def test_check_section_must_be_table(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_config(tmp, 'check = "nope"\n')
            with self.assertRaises(ConfigError):
                load_config(path)

    def test_no_config_file_returns_empty_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "nonexistent.toml"
            # 显式路径不存在 → 报错；自动查找找不到 → 空配置
            with self.assertRaises(ConfigError):
                load_config(path)
            self.assertTrue(RuleConfig().is_empty)


if __name__ == "__main__":
    unittest.main()
