//! Application-wide F8 shortcut with clean registration and shutdown.

use std::{
    sync::mpsc::{self, Receiver},
    thread::{self, JoinHandle},
};
use windows_sys::Win32::{
    System::Threading::GetCurrentThreadId,
    UI::{
        Input::KeyboardAndMouse::{MOD_NOREPEAT, RegisterHotKey, UnregisterHotKey, VK_F8},
        WindowsAndMessaging::{GetMessageW, MSG, PostThreadMessageW, WM_HOTKEY, WM_QUIT},
    },
};

pub struct Hotkey {
    events: Receiver<()>,
    thread_id: u32,
    thread: Option<JoinHandle<()>>,
}

impl Hotkey {
    pub fn register() -> Option<Self> {
        let (ready_tx, ready_rx) = mpsc::channel();
        let (event_tx, events) = mpsc::channel();
        let thread = thread::Builder::new()
            .name("prtsbox-hotkey".into())
            .spawn(move || {
                // SAFETY: thread-local message queue and null HWND are valid for RegisterHotKey.
                unsafe {
                    let id = GetCurrentThreadId();
                    let registered =
                        RegisterHotKey(std::ptr::null_mut(), 1, MOD_NOREPEAT, VK_F8 as u32) != 0;
                    let _ = ready_tx.send((id, registered));
                    if !registered {
                        return;
                    }
                    let mut message = MSG::default();
                    while GetMessageW(&mut message, std::ptr::null_mut(), 0, 0) > 0 {
                        if message.message == WM_HOTKEY {
                            let _ = event_tx.send(());
                        }
                    }
                    UnregisterHotKey(std::ptr::null_mut(), 1);
                }
            })
            .ok()?;
        let (thread_id, registered) = ready_rx.recv().ok()?;
        if !registered {
            let _ = thread.join();
            return None;
        }
        Some(Self {
            events,
            thread_id,
            thread: Some(thread),
        })
    }

    pub fn pressed(&self) -> bool {
        self.events.try_recv().is_ok()
    }
}

impl Drop for Hotkey {
    fn drop(&mut self) {
        // SAFETY: this ID came from GetCurrentThreadId in the registered worker.
        unsafe {
            PostThreadMessageW(self.thread_id, WM_QUIT, 0, 0);
        }
        if let Some(thread) = self.thread.take() {
            let _ = thread.join();
        }
    }
}
