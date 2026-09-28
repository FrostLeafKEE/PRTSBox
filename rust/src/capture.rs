//! Window capture and enumeration in physical client coordinates.

use anyhow::{Result, bail};
use image::{Rgba, RgbaImage};
use std::mem::size_of;
use windows_sys::Win32::{
    Foundation::{HWND, POINT, RECT},
    Graphics::{
        Dwm::DwmGetWindowAttribute,
        Gdi::{
            BI_RGB, BITMAPINFO, BITMAPINFOHEADER, BitBlt, ClientToScreen, CreateCompatibleBitmap,
            CreateCompatibleDC, DIB_RGB_COLORS, DeleteDC, DeleteObject, GetDC, GetDIBits,
            GetWindowDC, ReleaseDC, SRCCOPY, SelectObject,
        },
    },
    Storage::Xps::PrintWindow,
    UI::{
        HiDpi::GetDpiForWindow,
        WindowsAndMessaging::{
            EnumWindows, FindWindowW, GW_HWNDPREV, GWL_EXSTYLE, GetClientRect, GetForegroundWindow,
            GetWindow, GetWindowLongW, GetWindowRect, GetWindowTextLengthW, GetWindowTextW,
            GetWindowThreadProcessId, HWND_TOPMOST, IsIconic, IsWindow, IsWindowVisible,
            PW_RENDERFULLCONTENT, PostMessageW, SWP_NOACTIVATE, SWP_NOOWNERZORDER,
            SWP_NOSENDCHANGING, SetWindowDisplayAffinity, SetWindowPos, WDA_EXCLUDEFROMCAPTURE,
            WDA_NONE, WM_CLOSE, WS_EX_APPWINDOW, WS_EX_TOOLWINDOW, WS_EX_TOPMOST,
        },
    },
};

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct WindowInfo {
    pub hwnd: isize,
    pub title: String,
    pub left: i32,
    pub top: i32,
    pub width: i32,
    pub height: i32,
}

pub fn debug_windows_for_pid(pid: u32) -> Vec<String> {
    unsafe extern "system" fn visit(hwnd: HWND, state: isize) -> i32 {
        unsafe {
            let (pid, output) = &mut *(state as *mut (u32, Vec<String>));
            let mut owner = 0;
            GetWindowThreadProcessId(hwnd, &mut owner);
            if owner == *pid {
                let len = GetWindowTextLengthW(hwnd);
                let mut buffer = vec![0u16; (len + 1) as usize];
                GetWindowTextW(hwnd, buffer.as_mut_ptr(), len + 1);
                output.push(format!(
                    "{:?} visible={} title={}",
                    hwnd,
                    IsWindowVisible(hwnd),
                    String::from_utf16_lossy(&buffer[..len as usize])
                ));
            }
            1
        }
    }
    let mut state = (pid, Vec::new());
    unsafe {
        EnumWindows(
            Some(visit),
            (&mut state as *mut (u32, Vec<String>)) as isize,
        );
    }
    state.1
}

pub fn debug_geometry(id: isize) -> String {
    let hwnd = id as HWND;
    let mut outer = RECT::default();
    let outer_ok = unsafe { GetWindowRect(hwnd, &mut outer) } != 0;
    let client = window_info(id);
    format!(
        "hwnd={id} outer={} client={client:?}",
        if outer_ok {
            format!(
                "{},{},{}x{}",
                outer.left,
                outer.top,
                outer.right - outer.left,
                outer.bottom - outer.top
            )
        } else {
            "unavailable".into()
        }
    )
}

pub fn set_capture_affinity(title: &str, capturable: bool) {
    let wide = title.encode_utf16().chain(Some(0)).collect::<Vec<_>>();
    // SAFETY: title is a live NUL-terminated UTF-16 buffer. Failure is benign.
    unsafe {
        let hwnd = FindWindowW(std::ptr::null(), wide.as_ptr());
        if !hwnd.is_null() {
            let _ = SetWindowDisplayAffinity(
                hwnd,
                if capturable {
                    WDA_NONE
                } else {
                    WDA_EXCLUDEFROMCAPTURE
                },
            );
        }
    }
}

pub fn viewport_scale(title: &str, fallback: f32) -> f32 {
    let wide = title.encode_utf16().chain(Some(0)).collect::<Vec<_>>();
    // SAFETY: title is a live NUL-terminated UTF-16 buffer; a missing viewport uses the fallback.
    unsafe {
        let hwnd = FindWindowW(std::ptr::null(), wide.as_ptr());
        if hwnd.is_null() {
            fallback
        } else {
            (GetDpiForWindow(hwnd).max(96) as f32) / 96.0
        }
    }
}

pub fn place_overlay_above(title: &str, target: &WindowInfo) {
    let wide = title.encode_utf16().chain(Some(0)).collect::<Vec<_>>();
    // SAFETY: HWNDs are checked and SetWindowPos accepts this valid Win32 geometry.
    unsafe {
        let overlay = FindWindowW(std::ptr::null(), wide.as_ptr());
        if overlay.is_null() {
            return;
        }
        let target_hwnd = target.hwnd as HWND;
        let topmost = GetWindowLongW(target_hwnd, GWL_EXSTYLE) as u32 & WS_EX_TOPMOST != 0;
        let above = GetWindow(target_hwnd, GW_HWNDPREV);
        let predecessor = if topmost || above.is_null() {
            HWND_TOPMOST
        } else {
            above
        };
        SetWindowPos(
            overlay,
            predecessor,
            target.left,
            target.top,
            target.width,
            target.height,
            SWP_NOACTIVATE | SWP_NOOWNERZORDER | SWP_NOSENDCHANGING,
        );
    }
}

pub fn request_close(hwnd: isize) {
    // SAFETY: posting WM_CLOSE to a valid HWND asks the target to close normally.
    unsafe {
        PostMessageW(hwnd as HWND, WM_CLOSE, 0, 0);
    }
}

pub fn window_info(id: isize) -> Option<WindowInfo> {
    let hwnd = id as HWND;
    // SAFETY: Win32 validates HWND and writes only to local buffers.
    unsafe {
        if hwnd.is_null() || IsWindow(hwnd) == 0 {
            return None;
        }
        let mut rect = RECT::default();
        let mut origin = POINT::default();
        if GetClientRect(hwnd, &mut rect) == 0 || ClientToScreen(hwnd, &mut origin) == 0 {
            return None;
        }
        let (width, height) = (rect.right - rect.left, rect.bottom - rect.top);
        if width <= 0 || height <= 0 {
            return None;
        }
        let length = GetWindowTextLengthW(hwnd);
        let mut title = vec![0u16; (length + 1) as usize];
        let copied = GetWindowTextW(hwnd, title.as_mut_ptr(), length + 1);
        Some(WindowInfo {
            hwnd: id,
            title: String::from_utf16_lossy(&title[..copied.max(0) as usize]),
            left: origin.x,
            top: origin.y,
            width,
            height,
        })
    }
}

pub fn list_windows() -> Vec<WindowInfo> {
    unsafe extern "system" fn visit(hwnd: HWND, state: isize) -> i32 {
        // SAFETY: state points to a live Vec during this synchronous callback.
        unsafe {
            if IsWindowVisible(hwnd) == 0 || GetWindowTextLengthW(hwnd) <= 0 {
                return 1;
            }
            let mut owner = 0;
            GetWindowThreadProcessId(hwnd, &mut owner);
            if owner == std::process::id() {
                return 1;
            }
            let style = GetWindowLongW(hwnd, GWL_EXSTYLE) as u32;
            if style & WS_EX_TOOLWINDOW != 0 && style & WS_EX_APPWINDOW == 0 {
                return 1;
            }
            let mut cloaked = 0u32;
            if DwmGetWindowAttribute(hwnd, 14, (&mut cloaked as *mut u32).cast(), 4) == 0
                && cloaked != 0
            {
                return 1;
            }
            if let Some(info) = window_info(hwnd as isize)
                && info.width >= 80
                && info.height >= 60
            {
                (*(state as *mut Vec<WindowInfo>)).push(info);
            }
            1
        }
    }
    let mut windows: Vec<WindowInfo> = Vec::new();
    // SAFETY: callback and Vec outlive the synchronous EnumWindows call.
    unsafe { EnumWindows(Some(visit), (&mut windows as *mut Vec<WindowInfo>) as isize) };
    windows.sort_by_key(|window| window.title.to_lowercase());
    windows
}

pub fn capture_window(window: &WindowInfo) -> Result<RgbaImage> {
    let hwnd = window.hwnd as HWND;
    // SAFETY: all acquired GDI handles are checked and released by CaptureHandles.
    unsafe {
        if IsIconic(hwnd) != 0 {
            bail!("window is minimized");
        }
        let mut outer = RECT::default();
        if GetWindowRect(hwnd, &mut outer) == 0 {
            bail!("window rectangle unavailable");
        }
        let (width, height) = (outer.right - outer.left, outer.bottom - outer.top);
        if width <= 0 || height <= 0 {
            bail!("window has empty bounds");
        }
        let dc = GetWindowDC(hwnd);
        if dc.is_null() {
            bail!("GetWindowDC failed");
        }
        let memory_dc = CreateCompatibleDC(dc);
        if memory_dc.is_null() {
            ReleaseDC(hwnd, dc);
            bail!("CreateCompatibleDC failed");
        }
        let bitmap = CreateCompatibleBitmap(dc, width, height);
        if bitmap.is_null() {
            DeleteDC(memory_dc);
            ReleaseDC(hwnd, dc);
            bail!("CreateCompatibleBitmap failed");
        }
        let previous = SelectObject(memory_dc, bitmap);
        if previous.is_null() || previous as isize == -1 {
            DeleteObject(bitmap);
            DeleteDC(memory_dc);
            ReleaseDC(hwnd, dc);
            bail!("SelectObject failed");
        }
        let mut handles = CaptureHandles {
            hwnd,
            dc,
            memory_dc,
            bitmap,
            previous,
        };
        if PrintWindow(hwnd, memory_dc, PW_RENDERFULLCONTENT) == 0
            && PrintWindow(hwnd, memory_dc, 0) == 0
        {
            if std::env::var_os("PRTSBOX_TRACE_CAPTURE").is_some() {
                eprintln!(
                    "PrintWindow unavailable for {}: trying foreground screen capture",
                    window.title
                );
            }
            // Some GPU-rendered games reject PrintWindow. A desktop copy is valid only
            // while the requested window is foreground; otherwise it could return a
            // different app's pixels and translate unrelated content.
            if GetForegroundWindow() != hwnd {
                bail!("PrintWindow failed; focus the target window for screen capture");
            }
            let screen_dc = GetDC(std::ptr::null_mut());
            if screen_dc.is_null() {
                bail!("PrintWindow failed; GetDC(screen) failed");
            }
            let copied = BitBlt(
                memory_dc, 0, 0, width, height, screen_dc, outer.left, outer.top, SRCCOPY,
            );
            ReleaseDC(std::ptr::null_mut(), screen_dc);
            if copied == 0 {
                bail!("PrintWindow and screen capture failed");
            }
        }
        // GetDIBits requires the bitmap not to be selected into any DC.
        SelectObject(memory_dc, previous);
        handles.previous = std::ptr::null_mut();
        let mut info = BITMAPINFO::default();
        info.bmiHeader.biSize = size_of::<BITMAPINFOHEADER>() as u32;
        info.bmiHeader.biWidth = width;
        info.bmiHeader.biHeight = -height;
        info.bmiHeader.biPlanes = 1;
        info.bmiHeader.biBitCount = 32;
        info.bmiHeader.biCompression = BI_RGB;
        let length = (width as usize)
            .checked_mul(height as usize)
            .and_then(|n| n.checked_mul(4))
            .ok_or_else(|| anyhow::anyhow!("window is too large"))?;
        let mut pixels = vec![0u8; length];
        if GetDIBits(
            dc,
            bitmap,
            0,
            height as u32,
            pixels.as_mut_ptr().cast(),
            &mut info,
            DIB_RGB_COLORS,
        ) == 0
        {
            bail!("GetDIBits failed");
        }
        let left = (window.left - outer.left).clamp(0, width);
        let top = (window.top - outer.top).clamp(0, height);
        let right = (window.left - outer.left + window.width).clamp(left, width);
        let bottom = (window.top - outer.top + window.height).clamp(top, height);
        if right <= left || bottom <= top {
            bail!("client area is empty");
        }
        let mut image = RgbaImage::new((right - left) as u32, (bottom - top) as u32);
        for y in top..bottom {
            for x in left..right {
                let at = ((y * width + x) * 4) as usize;
                image.put_pixel(
                    (x - left) as u32,
                    (y - top) as u32,
                    Rgba([pixels[at + 2], pixels[at + 1], pixels[at], 255]),
                );
            }
        }
        Ok(image)
    }
}

struct CaptureHandles {
    hwnd: HWND,
    dc: *mut core::ffi::c_void,
    memory_dc: *mut core::ffi::c_void,
    bitmap: *mut core::ffi::c_void,
    previous: *mut core::ffi::c_void,
}

impl Drop for CaptureHandles {
    fn drop(&mut self) {
        // SAFETY: every handle was acquired in capture_window.
        unsafe {
            if !self.previous.is_null() {
                SelectObject(self.memory_dc, self.previous);
            }
            DeleteObject(self.bitmap);
            DeleteDC(self.memory_dc);
            ReleaseDC(self.hwnd, self.dc);
        }
    }
}
