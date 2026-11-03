#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fcitx5-phrase 的单元测试与集成测试。

运行:
    python3 -m unittest discover -s tests -v
不需要 fcitx5 正在运行；测试使用临时 HOME，不会碰你的真实词库。
"""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import fcitx5_phrase as fp  # noqa: E402


class CoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        os.environ["XDG_CACHE_HOME"] = str(Path(cls.tmp.name) / "cache")
        cls.pinyin_index, cls.wubi_index = fp.build_indexes()

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_segment(self):
        cases = {
            "weixin": "wei'xin",
            "zhongguo": "zhong'guo",
            "xian": "xian",
            "xi'an": "xi'an",
            "lvse": "lv'se",
            "nve": "nve",
        }
        for raw, want in cases.items():
            self.assertEqual(fp.segment_pinyin(raw, self.pinyin_index), want)

    def test_segment_invalid(self):
        with self.assertRaises(fp.PhraseError):
            fp.segment_pinyin("zzz", self.pinyin_index)

    def test_auto_pinyin_polyphonic(self):
        cases = {
            "重要": "zhong'yao",
            "银行": "yin'hang",
            "西安": "xi'an",
            "重庆": "chong'qing",
            "西安交通大学": "xi'an'jiao'tong'da'xue",
        }
        for word, want in cases.items():
            self.assertEqual(fp.auto_pinyin(word, self.pinyin_index), want)

    def test_derive_wubi(self):
        cases = {
            "微信": "tmwy",
            "汉字": "icpb",
            "重庆": "tgyd",
            "自定义词组": "tpyx",
        }
        for word, want in cases.items():
            self.assertEqual(fp.derive_wubi(word, self.wubi_index), want)

    def test_validate_phrase(self):
        with self.assertRaises(fp.PhraseError):
            fp.validate_phrase("")
        with self.assertRaises(fp.PhraseError):
            fp.validate_phrase("a b")

    def test_phrase_file_roundtrip(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "phrases.tsv"
            entries = [
                {"phrase": "微信", "pinyin": "wei'xin", "wubi": "tmwy", "weight": 100},
                {"phrase": "重庆", "pinyin": "chong'qing", "wubi": "", "weight": 120},
            ]
            fp.write_phrase_file(entries, path)
            self.assertEqual(fp.read_phrase_file(path), entries)

    def test_upsert(self):
        entries = [{"phrase": "微信", "pinyin": "wei'xin", "wubi": "", "weight": 1}]
        fp.upsert(entries, {"phrase": "微信", "pinyin": "wei'xin",
                            "wubi": "tmwy", "weight": 9})
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["weight"], 9)

    def test_wbpy_auto_section_converted(self):
        """libime 重建会丢 [Auto]；本工具应把它转成普通词条保留。"""
        lines = ["tmwy 微信", "[Auto]", "wdua 做并 1", "vegv 如有更好 1"]
        with mock.patch.object(fp, "dump_dict", return_value=lines):
            body, _ = fp._wbpy_data_lines(
                [{"phrase": "微信", "pinyin": "wei'xin",
                  "wubi": "tmwy", "weight": 100}])
        self.assertIn("wdua 做并", body)
        self.assertIn("vegv 如有更好", body)
        self.assertIn("tmwy 微信", body)
        self.assertNotIn("[Auto]", body)

    def test_wbpy_remove(self):
        lines = ["tmwy 微信", "icpb 汉字", "[Auto]", "wdua 做并 1"]
        with mock.patch.object(fp, "dump_dict", return_value=lines):
            body, _ = fp._wbpy_data_lines([], {"微信"})
        self.assertNotIn("tmwy 微信", body)
        self.assertIn("icpb 汉字", body)
        self.assertIn("wdua 做并", body)


class IntegrationTests(unittest.TestCase):
    """用临时 HOME 跑完整 add/apply/dump/remove 流程。"""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.home = Path(cls.tmp.name) / "home"
        cls.home.mkdir()
        cls.env = os.environ.copy()
        cls.env.update({
            "HOME": str(cls.home),
            "XDG_CACHE_HOME": str(Path(cls.tmp.name) / "cache"),
            "FCITX5_PHRASE_NO_PROCESS": "1",
        })

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def cli(self, *args, check=True):
        cp = subprocess.run(
            [sys.executable, str(REPO / "fcitx5-phrase"), *args],
            env=self.env, text=True, capture_output=True,
        )
        if check and cp.returncode != 0:
            self.fail(f"命令失败: {args}\nstdout={cp.stdout}\nstderr={cp.stderr}")
        return cp

    def test_full_flow(self):
        self.cli("add", "微信", "weixin", "--no-apply")
        self.cli("add", "汉字", "--no-apply")
        self.cli("apply", "--no-restart")

        pinyin = self.cli("dump", "pinyin").stdout
        wbpy = self.cli("dump", "wbpy").stdout
        self.assertIn("微信 wei'xin 100", pinyin)
        self.assertIn("汉字 han'zi 100", pinyin)
        self.assertIn("tmwy 微信", wbpy)
        self.assertIn("icpb 汉字", wbpy)

        # 删除后必须从二进制词库消失
        self.cli("remove", "汉字")
        self.assertNotIn("汉字", self.cli("dump", "pinyin").stdout)
        self.assertNotIn("汉字", self.cli("dump", "wbpy").stdout)

        # 幂等
        self.cli("apply", "--no-restart")
        self.assertEqual(self.cli("dump", "pinyin").stdout.count("微信"), 1)

    def test_legacy_wrapper(self):
        cp = subprocess.run(
            [str(REPO / "add_phrase.sh"), "teng'xun'wei'xin", "tmwy",
             "腾讯微信", "150"],
            env=self.env, text=True, capture_output=True,
        )
        self.assertEqual(cp.returncode, 0, cp.stderr)
        self.assertIn("腾讯微信 teng'xun'wei'xin 150",
                      self.cli("dump", "pinyin").stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
