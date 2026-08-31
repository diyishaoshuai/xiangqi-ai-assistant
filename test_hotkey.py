import threading
import ctypes
from ctypes import wintypes
import os
import unittest
from unittest.mock import Mock

from hotkey import GlobalF1Hotkey, HOTKEY_ID, MOD_NOREPEAT, VK_F1, WM_HOTKEY


class GlobalHotkeyTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "Windows thread message queue")
    def test_real_windows_message_queue_without_sending_keyboard_input(self):
        delivered = threading.Event()
        listener = GlobalF1Hotkey(delivered.set, key_pressed=lambda: False)
        listener.start()
        try:
            self.assertTrue(listener.ready.wait(2))
            if not listener.registered:
                self.skipTest("F1 is already registered by another application")
            api = ctypes.WinDLL("user32", use_last_error=True)
            api.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
            # A private message to our listener, not a simulated system keypress.
            self.assertTrue(api.PostThreadMessageW(listener.thread.native_id, WM_HOTKEY, HOTKEY_ID, 0))
            self.assertTrue(delivered.wait(1))
        finally:
            listener.close()
        self.assertFalse(listener.thread.is_alive())

    def test_native_message_delivery_uses_own_thread_and_releases_registration(self):
        api = Mock()
        api.RegisterHotKey.return_value = True
        delivered = threading.Event()
        callback_threads = []

        def peek(pointer, *_args):
            if delivered.is_set():
                return False
            pointer._obj.wParam = HOTKEY_ID
            return True

        def callback():
            callback_threads.append(threading.get_ident())
            delivered.set()

        api.PeekMessageW.side_effect = peek
        listener = GlobalF1Hotkey(callback, api=api, key_pressed=lambda: False)
        listener.start()
        try:
            self.assertTrue(delivered.wait(1))
            self.assertEqual(len(callback_threads), 1)
            self.assertNotEqual(callback_threads[0], threading.get_ident())
        finally:
            listener.close()
        api.RegisterHotKey.assert_called_once_with(None, HOTKEY_ID, MOD_NOREPEAT, VK_F1)
        api.UnregisterHotKey.assert_called_once_with(None, HOTKEY_ID)
        self.assertFalse(listener.thread.is_alive())

    def test_registration_conflict_falls_back_to_background_polling(self):
        api = Mock()
        api.RegisterHotKey.return_value = False
        pressed = threading.Event()
        delivered = threading.Event()
        listener = GlobalF1Hotkey(delivered.set, api=api, key_pressed=pressed.is_set)
        listener.start()
        try:
            self.assertTrue(listener.ready.wait(1))
            pressed.set()
            self.assertTrue(delivered.wait(1))
        finally:
            listener.close()
        api.UnregisterHotKey.assert_not_called()
        api.PeekMessageW.assert_not_called()


if __name__ == "__main__":
    unittest.main()
