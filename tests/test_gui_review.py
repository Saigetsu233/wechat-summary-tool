import threading
import tkinter as tk
import unittest
from unittest.mock import patch

import wechat_gui as gui


class ReviewControlsTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        with patch.object(gui, 'load_config', return_value={}):
            self.app = gui.WeChatSummaryApp(self.root)

    def tearDown(self):
        self.root.update()
        for timer in self.root.tk.splitlist(self.root.tk.call('after', 'info')):
            self.root.tk.call('after', 'cancel', timer)
        self.root.destroy()

    def test_initial_controls(self):
        self.assertTrue(self.app.preview_before_image_var.get())
        self.assertEqual(str(self.app.btn_redraw['state']), 'disabled')

    def test_templates_and_settings(self):
        self.root.geometry('1120x900')
        self.root.deiconify()
        self.root.update()
        self.assertEqual(self.app.settings_window.state(), 'withdrawn')
        for key, item in gui.TEMPLATES.items():
            self.app.template_var.set(item['name'])
            self.app._on_template_change()
            self.assertEqual(self.app._template_id(), key)
            self.assertEqual(self.app._current_illustration_mode(), 'poster')
            self.assertTrue(self.app.ai_topic_images_var.get())
        self.root.update()
        preview = self.app.template_hint
        self.assertLess(preview.winfo_rooty() + preview.winfo_height(),
                        self.root.winfo_rooty() + self.root.winfo_height())
        self.assertGreater(self.app.result_text.winfo_height(), 200)

    def test_redraw_reuses_digest_without_summary(self):
        digest = {'headline': 'Confirmed', 'group_name': 'Test'}
        self.app._last_digest = digest
        self.app.ai_topic_images_var.set(False)
        with patch.object(gui.filedialog, 'asksaveasfilename', return_value='redraw.png'), \
             patch.object(gui.threading, 'Thread') as thread:
            self.app._on_redraw()
        self.assertEqual(thread.call_args.kwargs['target'], self.app._render_digest)
        self.assertEqual(thread.call_args.kwargs['args'][0], dict(digest, template_id='handdrawn'))
        self.assertIsNot(thread.call_args.kwargs['args'][0], digest)
        thread.return_value.start.assert_called_once()

    def test_confirm_and_cancel_review(self):
        digest = {'headline': 'Original', 'lead': 'Summary', 'topics': [],
                  'mvp_rankings': [], 'achievements': [], 'quotes': []}
        def descendants(widget):
            for child in widget.winfo_children():
                yield child
                yield from descendants(child)
        for confirm in (True, False):
            results = []
            def worker():
                results.append(self.app._review_digest(digest))
                self.root.after(0, self.root.quit)
            def interact():
                windows = [w for w in self.root.winfo_children() if isinstance(w, tk.Toplevel)
                           and w is not self.app.settings_window]
                if not windows:
                    self.root.after(20, interact)
                    return
                widgets = list(descendants(windows[0]))
                box = next(w for w in widgets if isinstance(w, tk.Text))
                box.delete('1.0', 'end')
                box.insert('1.0', 'Edited')
                label = '确认并生成图片' if confirm else '取消'
                next(w for w in widgets if isinstance(w, gui.ttk.Button) and w['text'] == label).invoke()
            self.root.after(0, lambda: threading.Thread(target=worker, daemon=True).start())
            self.root.after(20, interact)
            watchdog = self.root.after(5000, self.root.quit)
            self.root.mainloop()
            self.root.after_cancel(watchdog)
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0]['headline'] if confirm else results[0], 'Edited' if confirm else None)
            self.assertEqual(digest['headline'], 'Original')


if __name__ == '__main__':
    unittest.main()
