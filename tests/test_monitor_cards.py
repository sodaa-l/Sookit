"""监控卡片的任务身份、取消/重试及布局回归；不触网、不生成截图。"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import time
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from PyQt6.QtWidgets import QApplication
from sookit.pages.monitor_page import MonitorPage


class MonitorCardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.manager = MagicMock()
        self.manager.add_task.return_value = SimpleNamespace(task_id="queue-1")
        self.patches = [
            patch("sookit.pages.monitor_page.TaskQueueManager.instance", return_value=self.manager),
            patch("sookit.pages.monitor_page.is_ytdlp_available", return_value=True),
            patch("sookit.pages.monitor_page.MonitorWorker"),
            patch("sookit.pages.monitor_page.load_download_config", return_value={
                "concurrent_fragments": 8, "use_aria2c": True, "aria2c_connections": 16}),
            patch("sookit.widgets.monitor_card.build_thumbnails", return_value=[]),
            patch.object(MonitorPage, "_sniff_and_start_worker"),
            patch.object(MonitorPage, "_download_best_thumbnail"),
            patch.object(MonitorPage, "_check_auto_action"),
        ]
        for p in self.patches:
            p.start()
        self.page = MonitorPage()
        self.page.resize(1100, 850)
        self.page.show()
        self.flush()

    def tearDown(self):
        for tid in list(self.page._task_cards):
            self.page._remove_task(tid)
        self.page.stop_all_workers()
        self.page.close()
        self.page.deleteLater()
        self.flush()
        for p in reversed(self.patches):
            p.stop()

    def flush(self):
        for _ in range(8):
            self.app.processEvents()

    def add(self, tid):
        self.page._add_single_task(tid, "https://youtu.be/abcdefghijk", tid)
        self.flush()
        return self.page._task_cards[tid]

    def upcoming(self, tid):
        info = dict(title="真实标题 " + tid, channel="测试频道", live_status="is_upcoming",
                    scheduled_start_time=time.time() + 3600)
        self.page._on_initial_sniff(tid, "url", "old-dir", tid, info)
        self.flush()
        return info

    def test_deleted_task_late_result_cannot_update_neighbour(self):
        self.add("a")
        b = self.add("b")
        self.page._remove_task("a")
        self.upcoming("a")
        self.page._update_task_status("a", "失败")
        self.page._on_download_ready("a", "url", "dir", "best", {})
        self.assertNotIn("a", self.page._task_info)
        self.assertEqual(b.title_label.text(), "b")
        self.manager.add_task.assert_not_called()

    def test_wait_start_uses_current_metadata_and_stops_timers(self):
        card = self.add("a")
        self.upcoming("a")
        self.assertTrue(card.start_btn.isVisible())
        wait_timer = self.page._wait_timers["a"]
        count_timer = self.page._countdown_timers["a"]
        self.page.out_dir.setText("D:/changed")
        with patch.object(self.page, "_start_monitor_now") as start:
            wait_timer.timeout.emit()
            self.assertEqual(start.call_args.args[2], "D:/changed")
            self.assertEqual(start.call_args.args[3], "真实标题 a")
        card.start_btn.click()
        self.assertFalse(wait_timer.isActive())
        self.assertFalse(count_timer.isActive())
        self.assertFalse(card.start_btn.isVisible())
        self.assertEqual(card.status_label.text(), "监控中")

    def test_download_preserves_channel_and_schedule_from_initial_sniff(self):
        card = self.add("a")
        self.upcoming("a")
        scheduled_text = card.time_label.text()
        self.page._on_download_ready("a", "url", "dir", "best", {}, {"title": "下载标题"})
        self.assertEqual(card.channel_label.text(), "频道：测试频道")
        self.assertEqual(card.time_label.text(), scheduled_text)
        self.assertEqual(self.manager.add_task.call_args.kwargs["metadata"]["channel"], "测试频道")

    def test_download_uses_new_uploader_when_available(self):
        card = self.add("a")
        self.upcoming("a")
        self.page._on_download_ready("a", "url", "dir", "best", {}, {"uploader": "新频道"})
        self.assertEqual(card.channel_label.text(), "频道：新频道")
        self.assertEqual(self.manager.add_task.call_args.kwargs["metadata"]["channel"], "新频道")

    def test_download_cancel_only_cancels_own_queue_task(self):
        card = self.add("a")
        self.add("b")
        self.page._on_download_ready("a", "url", "dir", "best", {}, {"title": "下载标题"})
        self.assertEqual(card.remove_btn.text(), "取消")
        card.remove_btn.click()
        self.assertNotIn("a", self.page._task_cards)
        self.assertIn("b", self.page._task_cards)
        self.manager.cancel_task.assert_called_once_with("queue-1")

    def test_completion_restores_delete_without_cancelling_completed_task(self):
        card = self.add("a")
        self.page._on_download_ready("a", "url", "dir", "best", {})
        self.page._on_queue_task_completed(SimpleNamespace(task_id="queue-1"))
        self.assertEqual(card.status_label.text(), "已完成")
        self.assertEqual(card.remove_btn.text(), "删除")
        card.remove_btn.click()
        self.manager.cancel_task.assert_not_called()
        self.assertEqual(self.page.task_stack.currentIndex(), 0)

    def test_failed_download_retries_and_restores_delete(self):
        card = self.add("a")
        self.page._on_download_ready("a", "url", "dir", "best", {})
        self.page._on_queue_task_failed(SimpleNamespace(task_id="queue-1"))
        self.assertEqual(card.status_label.text(), "等待重试")
        self.assertEqual(card.remove_btn.text(), "删除")
        self.assertIn("a", self.page._workers)
        self.assertNotIn("queue_task_id", self.page._task_info["a"])
        self.manager.remove_failed_task.assert_called_once_with("queue-1")

    def test_empty_state_and_scroll_do_not_move_input_or_resize_cards(self):
        initial_y = self.page.url_input.y()
        a = self.add("a")
        b = self.add("b")
        self.assertEqual(self.page.url_input.y(), initial_y)
        self.assertEqual(a.y(), b.y())
        self.assertGreater(b.x(), a.x())
        width = a.width()
        self.page.resize(1100, 650)
        self.flush()
        self.assertEqual(a.width(), width)
        self.assertGreaterEqual(a.height() - a.cover.height(), 180)
        self.assertLessEqual(a.remove_btn.geometry().bottom(), a.height())
        self.page._remove_task("a")
        self.page._remove_task("b")
        self.flush()
        self.assertEqual(self.page.task_stack.currentIndex(), 0)
        self.assertEqual(self.page.url_input.y(), initial_y)

    def test_shutdown_stops_wait_and_ignores_late_sniff(self):
        self.add("a")
        self.upcoming("a")
        timer = self.page._wait_timers["a"]
        self.page.stop_all_workers()
        self.assertFalse(timer.isActive())
        self.upcoming("a")
        self.assertFalse(self.page._wait_timers)
        self.page._start_waiting_task("a")
        self.assertFalse(self.page._workers)


if __name__ == "__main__":
    unittest.main()
