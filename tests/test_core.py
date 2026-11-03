#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fcitx5-phrase 的单元测试与集成测试。

运行:
    python3 -m unittest discover -s tests -v
不需要 fcitx5 正在运行；测试使用临时 HOME，不会碰你的真实词库。
"""

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
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


class WebTests(unittest.TestCase):
    """启动真实 HTTP 服务，验证 CSRF、增删、导入导出与退出。"""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.home = Path(cls.tmp.name) / "home"
        cls.home.mkdir()
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            cls.port = s.getsockname()[1]
        cls.base = f"http://127.0.0.1:{cls.port}"
        cls.env = os.environ.copy()
        cls.env.update({
            "HOME": str(cls.home),
            "XDG_CACHE_HOME": str(Path(cls.tmp.name) / "cache"),
            "FCITX5_PHRASE_NO_PROCESS": "1",
        })
        cls.proc = subprocess.Popen(
            [sys.executable, str(REPO / "fcitx5-phrase"), "web",
             "--port", str(cls.port), "--token", "testtoken", "--no-open"],
            env=cls.env, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        for _ in range(80):
            try:
                urllib.request.urlopen(cls.base + "/", timeout=0.5).read()
                break
            except Exception:
                time.sleep(0.1)
        else:
            raise RuntimeError("Web 服务未能启动")

    @classmethod
    def tearDownClass(cls):
        try:
            cls.proc.terminate()
            cls.proc.wait(timeout=5)
        except Exception:
            cls.proc.kill()
        cls.tmp.cleanup()

    def make_request(self, path, method="GET", data=None, token="testtoken",
                     host=None):
        headers = {}
        if token:
            headers["X-CSRF-Token"] = token
        if host:
            headers["Host"] = host
        body = None
        if data is not None:
            body = json.dumps(data).encode()
            headers["Content-Type"] = "application/json"
        return urllib.request.Request(self.base + path, data=body,
                                      headers=headers, method=method)

    def request(self, path, method="GET", data=None, token="testtoken",
                host=None):
        return urllib.request.urlopen(
            self.make_request(path, method, data, token, host), timeout=10)

    def expect_403(self, req):
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(req, timeout=5)
        self.assertEqual(cm.exception.code, 403)
        cm.exception.close()

    def test_csrf_and_host_checks(self):
        self.expect_403(urllib.request.Request(self.base + "/api/state"))
        self.expect_403(self.make_request("/api/state", token="wrong"))
        # Host 头校验（DNS rebinding 防护）
        self.expect_403(self.make_request("/", host="evil.example"))

    def test_add_remove_import_export(self):
        r = self.request("/api/add", "POST", {
            "phrase": "微信", "pinyin": "weixin", "wubi": "",
            "weight": 100, "apply": True,
        })
        entry = json.loads(r.read())["entry"]
        self.assertEqual(entry["pinyin"], "wei'xin")
        self.assertEqual(entry["wubi"], "tmwy")

        r = self.request("/api/import", "POST", {
            "text": "开源\tkaiyuan\n社区,shequ,,130",
            "replace": False, "apply": True,
        })
        self.assertEqual(json.loads(r.read())["imported"], 2)

        state = json.loads(self.request("/api/state").read())
        phrases = [e["phrase"] for e in state["entries"]]
        self.assertEqual(phrases, ["微信", "开源", "社区"])

        tsv = self.request("/api/export").read().decode()
        self.assertIn("微信\twei'xin\ttmwy\t100", tsv)

        r = self.request("/api/remove", "POST",
                         {"phrase": "微信", "apply": True})
        self.assertEqual(json.loads(r.read())["total"], 2)

        # 删除后二进制词库也要同步
        pinyin = self._dump("pinyin")
        self.assertNotIn("微信", pinyin)

    def _dump(self, target):
        cp = subprocess.run(
            [sys.executable, str(REPO / "fcitx5-phrase"), "dump", target],
            env=self.env, text=True, capture_output=True,
        )
        return cp.stdout

    def test_zz_shutdown(self):
        r = self.request("/api/shutdown", "POST", {})
        self.assertEqual(json.loads(r.read())["ok"], True)
        for _ in range(50):
            if self.proc.poll() is not None:
                break
            time.sleep(0.1)
        self.assertIsNotNone(self.proc.poll())


if __name__ == "__main__":
    unittest.main(verbosity=2)
