"""Zero-copy native Win32 D3D11/DXGI pop-up viewport with native hair toolbar & CUDA interop."""

import ctypes
import ctypes.wintypes as wt
import threading
import time

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
d3d11 = ctypes.WinDLL("d3d11", use_last_error=True)
nvcuda = ctypes.WinDLL("nvcuda", use_last_error=True)
comctl32 = ctypes.WinDLL("comctl32", use_last_error=True)
try:
    comctl32.InitCommonControls()
except Exception:
    pass

WS_OVERLAPPEDWINDOW = 0x00CF0000
WS_CHILD = 0x40000000
WS_CLIPSIBLINGS = 0x04000000
WS_CLIPCHILDREN = 0x02000000
WS_VISIBLE = 0x10000000
BS_PUSHBUTTON = 0x00000000
BS_CHECKBOX = 0x00000002
BS_PUSHLIKE = 0x00001000
BM_SETCHECK = 0x00F1
BST_UNCHECKED = 0
BST_CHECKED = 1
DEFAULT_GUI_FONT = 17
CW_USEDEFAULT = -2147483648
WM_DESTROY = 0x0002
WM_CLOSE = 0x0010
WM_SIZE = 0x0005
WM_SETFONT = 0x0030
WM_COMMAND = 0x0111
WM_HSCROLL = 0x0114
TBM_GETPOS = 0x0400
TBM_SETPOS = 0x0405
TBM_SETRANGE = 0x0406
WM_KEYDOWN = 0x0100
WM_MOUSEMOVE = 0x0200
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
WM_RBUTTONDOWN = 0x0204
WM_RBUTTONUP = 0x0205
WM_MBUTTONDOWN = 0x0207
WM_MBUTTONUP = 0x0208
WM_MOUSEWHEEL = 0x020A
WM_APP_PRESENT = 0x8001
SW_SHOW = 5
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
VK_SHIFT = 0x10
VK_CONTROL = 0x11
VK_MENU = 0x12

TOOLBAR_H = 38

DXGI_FORMAT_R8G8B8A8_UNORM = 28
DXGI_USAGE_RENDER_TARGET_OUTPUT = 0x20
DXGI_SWAP_EFFECT_FLIP_DISCARD = 4
DXGI_SWAP_EFFECT_DISCARD = 0
D3D_DRIVER_TYPE_HARDWARE = 1
D3D11_SDK_VERSION = 7
D3D11_CREATE_DEVICE_BGRA_SUPPORT = 0x20
D3D11_USAGE_DEFAULT = 0
D3D11_BIND_SHADER_RESOURCE = 0x8

CU_MEMORYTYPE_DEVICE = 2
CU_MEMORYTYPE_ARRAY = 3

WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)

TOOLBAR_ITEMS = [
    ("tool:comb", "Comb", True, 44),
    ("tool:surfaceComb", "Surf", True, 40),
    ("tool:grab", "Grab", True, 40),
    ("tool:smooth", "Smooth", True, 50),
    ("tool:clump", "Clump", True, 46),
    ("tool:part", "Part", True, 36),
    ("tool:puff", "Puff", True, 36),
    ("tool:lengthen", "Len+", True, 40),
    ("tool:shorten", "Len-", True, 38),
    ("tool:cut", "Cut", True, 34),
    ("tool:drawLock", "Draw", True, 40),
    ("tool:add", "Add", True, 36),
    ("tool:delete", "Del", True, 34),
    ("tool:pin", "Pin", True, 32),
    ("tool:unpin", "Unpin", True, 42),
    ("tool:physics", "Phys", True, 38),
    ("action:smoothAll", "SmoothAll", False, 64),
    ("action:undo", "Undo", False, 42),
    ("toggle:livePhysics", "LivePhys", True, 58),
    ("toggle:showPins", "Pins", True, 38),
    ("action:radiusDown", "Rad-", False, 38),
    ("action:radiusUp", "Rad+", False, 38),
    ("action:strengthDown", "Str-", False, 36),
    ("action:strengthUp", "Str+", False, 36),
    ("toggle:realtime", "RT/PT", True, 46),
    ("action:frame", "Frame", False, 46),
    ("action:done", "Done", False, 44),
]


class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wt.DWORD),
        ("Data2", wt.WORD),
        ("Data3", wt.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    ]

    @classmethod
    def from_str(cls, s):
        import uuid
        u = uuid.UUID(s)
        d4 = (ctypes.c_ubyte * 8)(*u.bytes[8:])
        return cls(u.time_low, u.time_mid, u.time_hi_version, d4)


IID_ID3D11Texture2D = GUID.from_str("{6f15aaf2-d208-4e89-9ab4-489535d34f9c}")


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wt.UINT),
        ("style", wt.UINT),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wt.HINSTANCE),
        ("hIcon", wt.HICON),
        ("hCursor", wt.HANDLE),
        ("hbrBackground", wt.HBRUSH),
        ("lpszMenuName", wt.LPCWSTR),
        ("lpszClassName", wt.LPCWSTR),
        ("hIconSm", wt.HICON),
    ]


class DXGI_SAMPLE_DESC(ctypes.Structure):
    _fields_ = [("Count", wt.UINT), ("Quality", wt.UINT)]


class DXGI_MODE_DESC(ctypes.Structure):
    _fields_ = [
        ("Width", wt.UINT),
        ("Height", wt.UINT),
        ("RefreshRate_Num", wt.UINT),
        ("RefreshRate_Den", wt.UINT),
        ("Format", wt.UINT),
        ("ScanlineOrdering", wt.UINT),
        ("Scaling", wt.UINT),
    ]


class DXGI_SWAP_CHAIN_DESC(ctypes.Structure):
    _fields_ = [
        ("BufferDesc", DXGI_MODE_DESC),
        ("SampleDesc", DXGI_SAMPLE_DESC),
        ("BufferUsage", wt.UINT),
        ("BufferCount", wt.UINT),
        ("OutputWindow", wt.HWND),
        ("Windowed", wt.BOOL),
        ("SwapEffect", wt.UINT),
        ("Flags", wt.UINT),
    ]


class D3D11_TEXTURE2D_DESC(ctypes.Structure):
    _fields_ = [
        ("Width", wt.UINT),
        ("Height", wt.UINT),
        ("MipLevels", wt.UINT),
        ("ArraySize", wt.UINT),
        ("Format", wt.UINT),
        ("SampleDesc", DXGI_SAMPLE_DESC),
        ("Usage", wt.UINT),
        ("BindFlags", wt.UINT),
        ("CPUAccessFlags", wt.UINT),
        ("MiscFlags", wt.UINT),
    ]


class D3D11_BOX(ctypes.Structure):
    _fields_ = [
        ("left", wt.UINT),
        ("top", wt.UINT),
        ("front", wt.UINT),
        ("right", wt.UINT),
        ("bottom", wt.UINT),
        ("back", wt.UINT),
    ]


class CUDA_MEMCPY2D(ctypes.Structure):
    _fields_ = [
        ("srcXInBytes", ctypes.c_size_t),
        ("srcY", ctypes.c_size_t),
        ("srcMemoryType", ctypes.c_int),
        ("srcHost", ctypes.c_void_p),
        ("srcDevice", ctypes.c_void_p),
        ("srcArray", ctypes.c_void_p),
        ("srcPitch", ctypes.c_size_t),
        ("dstXInBytes", ctypes.c_size_t),
        ("dstY", ctypes.c_size_t),
        ("dstMemoryType", ctypes.c_int),
        ("dstHost", ctypes.c_void_p),
        ("dstDevice", ctypes.c_void_p),
        ("dstArray", ctypes.c_void_p),
        ("dstPitch", ctypes.c_size_t),
        ("WidthInBytes", ctypes.c_size_t),
        ("Height", ctypes.c_size_t),
    ]


user32.DefWindowProcW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
user32.DefWindowProcW.restype = ctypes.c_ssize_t
user32.CreateWindowExW.argtypes = [
    wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD,
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    wt.HWND, wt.HMENU, wt.HINSTANCE, wt.LPVOID,
]
user32.CreateWindowExW.restype = wt.HWND
user32.RegisterClassExW.argtypes = [ctypes.POINTER(WNDCLASSEXW)]
user32.RegisterClassExW.restype = wt.ATOM
user32.UnregisterClassW.argtypes = [wt.LPCWSTR, wt.HINSTANCE]
user32.UnregisterClassW.restype = wt.BOOL
user32.LoadCursorW.argtypes = [wt.HINSTANCE, wt.LPCWSTR]
user32.LoadCursorW.restype = wt.HANDLE
user32.ShowWindow.argtypes = [wt.HWND, ctypes.c_int]
user32.ShowWindow.restype = wt.BOOL
user32.UpdateWindow.argtypes = [wt.HWND]
user32.UpdateWindow.restype = wt.BOOL
user32.DestroyWindow.argtypes = [wt.HWND]
user32.DestroyWindow.restype = wt.BOOL
user32.GetMessageW.argtypes = [ctypes.POINTER(wt.MSG), wt.HWND, wt.UINT, wt.UINT]
user32.GetMessageW.restype = wt.BOOL
user32.PostMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
user32.PostMessageW.restype = wt.BOOL
user32.SendMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
user32.SendMessageW.restype = ctypes.c_ssize_t
user32.SetWindowPos.argtypes = [wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.UINT]
user32.SetWindowPos.restype = wt.BOOL
user32.SetFocus.argtypes = [wt.HWND]
user32.SetFocus.restype = wt.HWND
user32.SetCapture.argtypes = [wt.HWND]
user32.SetCapture.restype = wt.HWND
user32.ReleaseCapture.argtypes = []
user32.ReleaseCapture.restype = wt.BOOL
user32.GetKeyState.argtypes = [ctypes.c_int]
user32.GetKeyState.restype = ctypes.c_short
user32.GetClientRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
user32.GetClientRect.restype = wt.BOOL
user32.ScreenToClient.argtypes = [wt.HWND, ctypes.POINTER(wt.POINT)]
user32.ScreenToClient.restype = wt.BOOL
user32.SetWindowTextW.argtypes = [wt.HWND, wt.LPCWSTR]
user32.SetWindowTextW.restype = wt.BOOL
gdi32.GetStockObject.argtypes = [ctypes.c_int]
gdi32.GetStockObject.restype = wt.HGDIOBJ
gdi32.CreateSolidBrush.argtypes = [wt.COLORREF]
gdi32.CreateSolidBrush.restype = wt.HBRUSH
kernel32.GetModuleHandleW.argtypes = [wt.LPCWSTR]
kernel32.GetModuleHandleW.restype = wt.HMODULE

nvcuda.cuCtxGetCurrent.argtypes = [ctypes.POINTER(ctypes.c_void_p)]
nvcuda.cuCtxGetCurrent.restype = ctypes.c_int
nvcuda.cuCtxSetCurrent.argtypes = [ctypes.c_void_p]
nvcuda.cuCtxSetCurrent.restype = ctypes.c_int
nvcuda.cuEventCreate.argtypes = [ctypes.POINTER(ctypes.c_void_p), wt.UINT]
nvcuda.cuEventCreate.restype = ctypes.c_int
nvcuda.cuEventDestroy_v2.argtypes = [ctypes.c_void_p]
nvcuda.cuEventDestroy_v2.restype = ctypes.c_int
nvcuda.cuEventRecord.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
nvcuda.cuEventRecord.restype = ctypes.c_int
nvcuda.cuStreamWaitEvent.argtypes = [ctypes.c_void_p, ctypes.c_void_p, wt.UINT]
nvcuda.cuStreamWaitEvent.restype = ctypes.c_int
nvcuda.cuGraphicsD3D11RegisterResource.argtypes = [ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p, wt.UINT]
nvcuda.cuGraphicsD3D11RegisterResource.restype = ctypes.c_int
nvcuda.cuGraphicsMapResources.argtypes = [wt.UINT, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p]
nvcuda.cuGraphicsMapResources.restype = ctypes.c_int
nvcuda.cuGraphicsSubResourceGetMappedArray.argtypes = [ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p, wt.UINT, wt.UINT]
nvcuda.cuGraphicsSubResourceGetMappedArray.restype = ctypes.c_int
nvcuda.cuMemcpy2DAsync_v2.argtypes = [ctypes.POINTER(CUDA_MEMCPY2D), ctypes.c_void_p]
nvcuda.cuMemcpy2DAsync_v2.restype = ctypes.c_int
nvcuda.cuGraphicsUnmapResources.argtypes = [wt.UINT, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p]
nvcuda.cuGraphicsUnmapResources.restype = ctypes.c_int
nvcuda.cuGraphicsUnregisterResource.argtypes = [ctypes.c_void_p]
nvcuda.cuGraphicsUnregisterResource.restype = ctypes.c_int

d3d11.D3D11CreateDeviceAndSwapChain.argtypes = [
    ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, wt.UINT,
    ctypes.c_void_p, wt.UINT, wt.UINT,
    ctypes.POINTER(DXGI_SWAP_CHAIN_DESC),
    ctypes.POINTER(ctypes.c_void_p),
    ctypes.POINTER(ctypes.c_void_p),
    ctypes.POINTER(wt.UINT),
    ctypes.POINTER(ctypes.c_void_p),
]
d3d11.D3D11CreateDeviceAndSwapChain.restype = ctypes.c_long


def _com_call(ptr, slot, restype, argtypes, *args):
    vtbl = ctypes.cast(ptr, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    fn_type = ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)
    fn = fn_type(vtbl[slot])
    return fn(ptr, *args)


def _com_release(ptr):
    if ptr:
        _com_call(ptr, 2, wt.ULONG, [])


def _check_cu(res, label):
    if res != 0:
        raise RuntimeError(f"CUDA interop {label} failed with error code {res}")


class NativeCudaViewport:
    """Standalone Win32 D3D11/DXGI pop-up window with native hair toolbar & zero-copy CUDA presentation."""

    def __init__(self, title="OrchFlow Hair Grooming & Live Physics", on_input=None):
        self.title = title
        self.on_input = on_input
        self._hwnd = None
        self._canvas_hwnd = None
        self._status_hwnd = None
        self._btn_hwnds = {}
        self._active_tool = "comb"
        self._toggles = {"livePhysics": False, "showPins": False, "realtime": True}
        self._canvas_size = (1180, 760)
        self._last_move_emit = 0.0
        self._last_hover_norm = (0.5, 0.5)
        self._thread = None
        self._ready = threading.Event()
        self._closed = True
        self._open_error = None
        self._device = None
        self._context = None
        self._swapchain = None
        self._tex = None
        self._cu_resource = None
        self._cu_ctx = None
        self._cu_event = None
        self._tex_w = 0
        self._tex_h = 0
        self._pending_resize = None
        self._pending_frame = None
        self._pending_status = None
        self._present_done = threading.Event()
        self._present_error = None
        self._wndproc_ref = None
        self._canvas_wndproc_ref = None
        self._bg_brush = None

    def is_open(self):
        return (not self._closed) and (self._hwnd is not None)

    def parent_hwnd(self):
        return 0

    def canvas_size(self):
        return self._canvas_size

    def last_hover_norm(self):
        return self._last_hover_norm

    def set_status(self, text):
        self._pending_status = str(text)

    def set_rect(self, x, y, width, height):
        pass

    def open(self, width=1180, height=760, parent_hwnd=0, x=0, y=0):
        width = max(640, int(width))
        height = max(480, int(height))
        if self.is_open():
            return
        cu_ctx = ctypes.c_void_p()
        _check_cu(nvcuda.cuCtxGetCurrent(ctypes.byref(cu_ctx)), "cuCtxGetCurrent")
        if not cu_ctx.value:
            raise RuntimeError("No active CUDA context when opening native viewport")
        self._cu_ctx = cu_ctx.value
        ev = ctypes.c_void_p()
        _check_cu(nvcuda.cuEventCreate(ctypes.byref(ev), 2), "cuEventCreate")
        self._cu_event = ev.value

        self._closed = False
        self._open_error = None
        self._ready.clear()
        self._thread = threading.Thread(
            target=self._window_loop, args=(width, height), daemon=True
        )
        self._thread.start()
        self._ready.wait(timeout=5.0)
        if self._open_error is not None:
            raise self._open_error

    def _window_loop(self, width, height):
        hinst = kernel32.GetModuleHandleW(None)
        cls_name = f"OrchFlowNativeHairWin_{id(self)}"
        canvas_cls = f"OrchFlowNativeHairCanvas_{id(self)}"
        try:
            self._window_loop_inner(hinst, cls_name, canvas_cls, width, height)
        except Exception as exc:
            import sys, traceback
            traceback.print_exc(file=sys.stderr)
            self._open_error = exc
            self._closed = True
            self._ready.set()

    def _sync_button_states(self):
        for idx, (cmd_id, _, is_toggle, _) in enumerate(TOOLBAR_ITEMS, start=100):
            bh = self._btn_hwnds.get(idx)
            if not bh or not is_toggle:
                continue
            if cmd_id.startswith("tool:"):
                checked = (cmd_id.split(":", 1)[1] == self._active_tool)
            elif cmd_id.startswith("toggle:"):
                checked = bool(self._toggles.get(cmd_id.split(":", 1)[1], False))
            else:
                checked = False
            user32.SendMessageW(bh, BM_SETCHECK, BST_CHECKED if checked else BST_UNCHECKED, 0)

    def _window_loop_inner(self, hinst, cls_name, canvas_cls, width, height):
        _check_cu(nvcuda.cuCtxSetCurrent(self._cu_ctx), "cuCtxSetCurrent")
        self._bg_brush = gdi32.CreateSolidBrush(0x001E1915)

        def _main_wnd_proc(hwnd, msg, wp, lp):
            try:
                return _main_wnd_proc_impl(hwnd, msg, wp, lp)
            except Exception:
                import sys, traceback
                traceback.print_exc(file=sys.stderr)
                return user32.DefWindowProcW(hwnd, msg, wp, lp)

        def _main_wnd_proc_impl(hwnd, msg, wp, lp):
            if msg == WM_CLOSE:
                self._closed = True
                self._present_done.set()
                if self.on_input is not None:
                    self.on_input({"type": "toolbar", "action": "closed"})
                user32.DestroyWindow(hwnd)
                return 0
            if msg == WM_DESTROY:
                self._closed = True
                self._hwnd = None
                self._canvas_hwnd = None
                self._present_done.set()
                user32.PostQuitMessage(0)
                return 0
            if msg == WM_SIZE:
                nw = max(64, lp & 0xFFFF)
                nh = max(64, (lp >> 16) & 0xFFFF)
                ch = max(64, nh - TOOLBAR_H)
                self._canvas_size = (nw, ch)
                if self._canvas_hwnd:
                    user32.SetWindowPos(self._canvas_hwnd, None, 0, TOOLBAR_H, nw, ch, SWP_NOZORDER | SWP_NOACTIVATE)
                if self._status_hwnd:
                    sx = max(6, nw - 250)
                    user32.SetWindowPos(self._status_hwnd, None, sx, 9, 244, TOOLBAR_H - 16, SWP_NOZORDER | SWP_NOACTIVATE)
                if self.on_input is not None:
                    self.on_input({"type": "resize", "width": nw, "height": ch})
                return 0
            if msg in (WM_KEYDOWN, WM_MOUSEWHEEL) and self.on_input is not None and self._canvas_hwnd:
                self._emit_win32_input(self._canvas_hwnd, msg, wp, lp)
                return 0
            if msg == WM_HSCROLL and getattr(self, "_den_hwnd", None) and int(lp) == int(self._den_hwnd):
                val = max(16, min(300, int(user32.SendMessageW(self._den_hwnd, TBM_GETPOS, 0, 0))))
                if val != getattr(self, "_draw_density", 120):
                    self._draw_density = val
                    if self.on_input is not None:
                        self.on_input({
                            "type": "toolbar",
                            "command": "set:drawDensity",
                            "drawDensity": val,
                        })
                return 0
            if msg == WM_COMMAND:
                ctrl_id = int(wp) & 0xFFFF
                notify_code = (int(wp) >> 16) & 0xFFFF
                if ctrl_id == 200 and notify_code == 1 and getattr(self, "_vpct_hwnd", None):
                    sel = int(user32.SendMessageW(self._vpct_hwnd, 0x0147, 0, 0))
                    pct = 100 if sel <= 0 else (50 if sel == 1 else 25)
                    self._vertex_pct = pct
                    if self._canvas_hwnd:
                        user32.SetFocus(self._canvas_hwnd)
                    if self.on_input is not None:
                        self.on_input({
                            "type": "toolbar",
                            "command": "set:vertexPct",
                            "vertexPct": pct,
                        })
                    return 0
                if ctrl_id == 201 and notify_code == 1 and getattr(self, "_tip_hwnd", None):
                    sel = int(user32.SendMessageW(self._tip_hwnd, 0x0147, 0, 0))
                    tip_opts = (10, 25, 50, 75, 100)
                    tpct = tip_opts[sel] if 0 <= sel < len(tip_opts) else 25
                    self._draw_tip_pct = tpct
                    if self._canvas_hwnd:
                        user32.SetFocus(self._canvas_hwnd)
                    if self.on_input is not None:
                        self.on_input({
                            "type": "toolbar",
                            "command": "set:drawTipPct",
                            "drawTipPct": tpct,
                        })
                    return 0
                idx = ctrl_id - 100
                if 0 <= idx < len(TOOLBAR_ITEMS):
                    cmd_id = TOOLBAR_ITEMS[idx][0]
                    if cmd_id == "action:done":
                        user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
                        return 0
                    if cmd_id.startswith("tool:"):
                        self._active_tool = cmd_id.split(":", 1)[1]
                    elif cmd_id.startswith("toggle:"):
                        k = cmd_id.split(":", 1)[1]
                        self._toggles[k] = not self._toggles.get(k, False)
                    self._sync_button_states()
                    if self._canvas_hwnd:
                        user32.SetFocus(self._canvas_hwnd)
                    if self.on_input is not None:
                        self.on_input({
                            "type": "toolbar",
                            "command": cmd_id,
                            "tool": self._active_tool,
                            "toggles": dict(self._toggles),
                        })
                return 0
            return user32.DefWindowProcW(hwnd, msg, wp, lp)

        def _canvas_wnd_proc(hwnd, msg, wp, lp):
            try:
                return _canvas_wnd_proc_impl(hwnd, msg, wp, lp)
            except Exception:
                import sys, traceback
                traceback.print_exc(file=sys.stderr)
                return user32.DefWindowProcW(hwnd, msg, wp, lp)

        def _canvas_wnd_proc_impl(hwnd, msg, wp, lp):
            if msg == WM_APP_PRESENT:
                frame = self._pending_frame
                self._pending_frame = None
                if frame is not None:
                    try:
                        self._present_on_window_thread(*frame)
                        self._present_error = None
                    except Exception as exc:
                        self._present_error = exc
                    finally:
                        self._present_done.set()
                return 0
            if msg == WM_SIZE:
                nw = lp & 0xFFFF
                nh = (lp >> 16) & 0xFFFF
                if nw > 16 and nh > 16:
                    self._pending_resize = (nw, nh)
                return 0
            if msg in (
                WM_MOUSEMOVE, WM_LBUTTONDOWN, WM_LBUTTONUP,
                WM_RBUTTONDOWN, WM_RBUTTONUP, WM_MBUTTONDOWN, WM_MBUTTONUP,
                WM_MOUSEWHEEL, WM_KEYDOWN,
            ) and self.on_input is not None:
                self._emit_win32_input(hwnd, msg, wp, lp)
                if msg in (WM_LBUTTONDOWN, WM_RBUTTONDOWN, WM_MBUTTONDOWN):
                    user32.SetFocus(hwnd)
                    user32.SetCapture(hwnd)
                elif msg in (WM_LBUTTONUP, WM_RBUTTONUP, WM_MBUTTONUP):
                    user32.ReleaseCapture()
                return 0
            return user32.DefWindowProcW(hwnd, msg, wp, lp)

        self._wndproc_ref = WNDPROC(_main_wnd_proc)
        self._canvas_wndproc_ref = WNDPROC(_canvas_wnd_proc)

        wc = WNDCLASSEXW()
        wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
        wc.lpfnWndProc = self._wndproc_ref
        wc.hInstance = hinst
        wc.hCursor = user32.LoadCursorW(None, ctypes.cast(32512, wt.LPCWSTR))
        wc.hbrBackground = self._bg_brush
        wc.lpszClassName = cls_name
        user32.RegisterClassExW(ctypes.byref(wc))

        wc_c = WNDCLASSEXW()
        wc_c.cbSize = ctypes.sizeof(WNDCLASSEXW)
        wc_c.lpfnWndProc = self._canvas_wndproc_ref
        wc_c.hInstance = hinst
        wc_c.hCursor = user32.LoadCursorW(None, ctypes.cast(32512, wt.LPCWSTR))
        wc_c.lpszClassName = canvas_cls
        user32.RegisterClassExW(ctypes.byref(wc_c))

        hwnd = user32.CreateWindowExW(
            0, cls_name, self.title, WS_OVERLAPPEDWINDOW | WS_VISIBLE | WS_CLIPCHILDREN,
            CW_USEDEFAULT, CW_USEDEFAULT, width, height + TOOLBAR_H,
            None, None, hinst, None,
        )
        if not hwnd:
            self._open_error = RuntimeError("Failed to create Win32 native viewport window")
            self._closed = True
            self._ready.set()
            return
        self._hwnd = hwnd

        # Create toolbar buttons along the top strip
        gui_font = gdi32.GetStockObject(DEFAULT_GUI_FONT)
        bx = 6
        for idx, (_cmd, label, is_toggle, bw) in enumerate(TOOLBAR_ITEMS, start=100):
            bstyle = WS_CHILD | WS_VISIBLE | (BS_CHECKBOX | BS_PUSHLIKE if is_toggle else BS_PUSHBUTTON)
            bh = user32.CreateWindowExW(
                0, "BUTTON", label, bstyle,
                bx, 6, bw, TOOLBAR_H - 12,
                hwnd, ctypes.c_void_p(idx), hinst, None,
            )
            if bh:
                user32.SendMessageW(bh, WM_SETFONT, gui_font, 1)
                self._btn_hwnds[idx] = bh
            bx += bw + 3
        self._sync_button_states()
        self._vertex_pct = 100
        self._vpct_hwnd = user32.CreateWindowExW(
            0, "COMBOBOX", "", WS_CHILD | WS_VISIBLE | 0x0003,
            bx + 2, 6, 56, 120,
            hwnd, ctypes.c_void_p(200), hinst, None,
        )
        if self._vpct_hwnd:
            user32.SendMessageW(self._vpct_hwnd, WM_SETFONT, gui_font, 1)
            for opt in ("100%", "50%", "25%"):
                buf = ctypes.create_unicode_buffer(opt)
                user32.SendMessageW(self._vpct_hwnd, 0x0143, 0, ctypes.addressof(buf))
            user32.SendMessageW(self._vpct_hwnd, 0x014E, 0, 0)
            bx += 60
        self._draw_tip_pct = 25
        self._tip_hwnd = user32.CreateWindowExW(
            0, "COMBOBOX", "", WS_CHILD | WS_VISIBLE | 0x0003,
            bx + 2, 6, 70, 140,
            hwnd, ctypes.c_void_p(201), hinst, None,
        )
        if self._tip_hwnd:
            user32.SendMessageW(self._tip_hwnd, WM_SETFONT, gui_font, 1)
            for opt in ("Tip 10%", "Tip 25%", "Tip 50%", "Tip 75%", "Tip 100%"):
                buf = ctypes.create_unicode_buffer(opt)
                user32.SendMessageW(self._tip_hwnd, 0x0143, 0, ctypes.addressof(buf))
            user32.SendMessageW(self._tip_hwnd, 0x014E, 1, 0)
            bx += 74
        self._draw_density = 120
        self._den_lbl_hwnd = user32.CreateWindowExW(
            0, "STATIC", "Den", WS_CHILD | WS_VISIBLE,
            bx + 2, 9, 24, TOOLBAR_H - 16,
            hwnd, None, hinst, None,
        )
        if self._den_lbl_hwnd:
            user32.SendMessageW(self._den_lbl_hwnd, WM_SETFONT, gui_font, 1)
            bx += 26
        self._den_hwnd = user32.CreateWindowExW(
            0, "msctls_trackbar32", "", WS_CHILD | WS_VISIBLE | 0x0010,
            bx, 6, 86, TOOLBAR_H - 12,
            hwnd, ctypes.c_void_p(202), hinst, None,
        )
        if self._den_hwnd:
            user32.SendMessageW(self._den_hwnd, TBM_SETRANGE, 1, (300 << 16) | 16)
            user32.SendMessageW(self._den_hwnd, TBM_SETPOS, 1, 120)
            bx += 90
        self._status_hwnd = user32.CreateWindowExW(
            0, "STATIC", "Rad: 0.060 | Str: 1.00 | comb",
            WS_CHILD | WS_VISIBLE,
            bx + 4, 9, 240, TOOLBAR_H - 16,
            hwnd, None, hinst, None,
        )
        if self._status_hwnd:
            user32.SendMessageW(self._status_hwnd, WM_SETFONT, gui_font, 1)

        # Create child D3D11 canvas HWND below toolbar
        crect = wt.RECT()
        user32.GetClientRect(hwnd, ctypes.byref(crect))
        cw = max(64, crect.right - crect.left)
        ch = max(64, crect.bottom - crect.top - TOOLBAR_H)
        canvas_hwnd = user32.CreateWindowExW(
            0, canvas_cls, "", WS_CHILD | WS_VISIBLE | WS_CLIPSIBLINGS,
            0, TOOLBAR_H, cw, ch,
            hwnd, None, hinst, None,
        )
        if not canvas_hwnd:
            self._open_error = RuntimeError("Failed to create native D3D11 canvas child window")
            self._closed = True
            user32.DestroyWindow(hwnd)
            self._ready.set()
            return
        self._canvas_hwnd = canvas_hwnd
        self._canvas_size = (cw, ch)

        try:
            self._init_d3d11(canvas_hwnd, cw, ch)
        except Exception as exc:
            self._open_error = exc
            self._closed = True
            user32.DestroyWindow(hwnd)
            self._ready.set()
            return

        user32.ShowWindow(hwnd, SW_SHOW)
        user32.UpdateWindow(hwnd)
        self._ready.set()

        msg = wt.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

        self._cleanup_d3d11()
        user32.UnregisterClassW(canvas_cls, hinst)
        user32.UnregisterClassW(cls_name, hinst)

    def _emit_win32_input(self, hwnd, msg, wp, lp):
        rect = wt.RECT()
        user32.GetClientRect(hwnd, ctypes.byref(rect))
        cw = max(1, rect.right - rect.left)
        ch = max(1, rect.bottom - rect.top)
        sx = ctypes.c_short(lp & 0xFFFF).value
        sy = ctypes.c_short((lp >> 16) & 0xFFFF).value
        shift_down = bool(user32.GetKeyState(VK_SHIFT) & 0x8000)
        ctrl_down = bool(user32.GetKeyState(VK_CONTROL) & 0x8000)
        alt_down = bool(user32.GetKeyState(VK_MENU) & 0x8000)
        if msg == WM_KEYDOWN:
            vk = int(wp)
            key = "[" if vk == 0xDB else ("]" if vk == 0xDD else ("Escape" if vk == 0x1B else ""))
            if key:
                self.on_input({
                    "type": "keydown", "key": key,
                    "shiftKey": shift_down, "ctrlKey": ctrl_down, "altKey": alt_down,
                })
            return
        if msg == WM_MOUSEWHEEL:
            delta = ctypes.c_short((wp >> 16) & 0xFFFF).value
            pt = wt.POINT(sx, sy)
            user32.ScreenToClient(hwnd, ctypes.byref(pt))
            self.on_input({
                "type": "wheel", "x": int(pt.x), "y": int(pt.y),
                "width": cw, "height": ch, "deltaY": float(-delta),
                "shiftKey": shift_down, "ctrlKey": ctrl_down, "altKey": alt_down,
            })
            return
        kind_map = {
            WM_MOUSEMOVE: ("mousemove", -1),
            WM_LBUTTONDOWN: ("mousedown", 0),
            WM_LBUTTONUP: ("mouseup", 0),
            WM_MBUTTONDOWN: ("mousedown", 1),
            WM_MBUTTONUP: ("mouseup", 1),
            WM_RBUTTONDOWN: ("mousedown", 2),
            WM_RBUTTONUP: ("mouseup", 2),
        }
        kind, btn = kind_map.get(msg, ("", -1))
        if kind:
            self._last_hover_norm = (min(1.0, max(0.0, sx / cw)), min(1.0, max(0.0, sy / ch)))
            if kind == "mousemove":
                now = time.monotonic()
                if now - self._last_move_emit < 0.015:
                    return
                self._last_move_emit = now
            self.on_input({
                "type": kind, "button": btn, "buttons": int(wp) & 0x1F,
                "x": int(sx), "y": int(sy), "width": cw, "height": ch,
                "shiftKey": shift_down, "ctrlKey": ctrl_down, "altKey": alt_down,
            })

    def _init_d3d11(self, hwnd, width, height):
        scd = DXGI_SWAP_CHAIN_DESC()
        scd.BufferDesc.Width = width
        scd.BufferDesc.Height = height
        scd.BufferDesc.Format = DXGI_FORMAT_R8G8B8A8_UNORM
        scd.SampleDesc.Count = 1
        scd.BufferUsage = DXGI_USAGE_RENDER_TARGET_OUTPUT
        scd.BufferCount = 2
        scd.OutputWindow = hwnd
        scd.Windowed = True
        scd.SwapEffect = DXGI_SWAP_EFFECT_FLIP_DISCARD

        swapchain = ctypes.c_void_p()
        device = ctypes.c_void_p()
        context = ctypes.c_void_p()
        feature_level = wt.UINT()
        hr = d3d11.D3D11CreateDeviceAndSwapChain(
            None, D3D_DRIVER_TYPE_HARDWARE, None, D3D11_CREATE_DEVICE_BGRA_SUPPORT,
            None, 0, D3D11_SDK_VERSION,
            ctypes.byref(scd), ctypes.byref(swapchain), ctypes.byref(device),
            ctypes.byref(feature_level), ctypes.byref(context),
        )
        if hr != 0:
            scd.SwapEffect = DXGI_SWAP_EFFECT_DISCARD
            scd.BufferCount = 1
            hr = d3d11.D3D11CreateDeviceAndSwapChain(
                None, D3D_DRIVER_TYPE_HARDWARE, None, D3D11_CREATE_DEVICE_BGRA_SUPPORT,
                None, 0, D3D11_SDK_VERSION,
                ctypes.byref(scd), ctypes.byref(swapchain), ctypes.byref(device),
                ctypes.byref(feature_level), ctypes.byref(context),
            )
        if hr != 0:
            raise RuntimeError(f"D3D11CreateDeviceAndSwapChain failed: 0x{hr & 0xffffffff:08x}")

        self._device = device.value
        self._context = context.value
        self._swapchain = swapchain.value
        self._tex_w = 0
        self._tex_h = 0

    def _ensure_texture(self, width, height):
        if self._tex is not None and self._tex_w == width and self._tex_h == height:
            return
        if self._cu_resource is not None:
            nvcuda.cuGraphicsUnregisterResource(self._cu_resource)
            self._cu_resource = None
        if self._tex is not None:
            _com_release(self._tex)
            self._tex = None

        desc = D3D11_TEXTURE2D_DESC()
        desc.Width = width
        desc.Height = height
        desc.MipLevels = 1
        desc.ArraySize = 1
        desc.Format = DXGI_FORMAT_R8G8B8A8_UNORM
        desc.SampleDesc.Count = 1
        desc.Usage = D3D11_USAGE_DEFAULT
        desc.BindFlags = D3D11_BIND_SHADER_RESOURCE
        tex = ctypes.c_void_p()
        hr = _com_call(
            self._device, 5, ctypes.c_long,
            [ctypes.POINTER(D3D11_TEXTURE2D_DESC), ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)],
            ctypes.byref(desc), None, ctypes.byref(tex),
        )
        if hr != 0 or not tex.value:
            raise RuntimeError(f"CreateTexture2D({width}x{height}) failed: 0x{hr & 0xffffffff:08x}")
        self._tex = tex.value
        self._tex_w = width
        self._tex_h = height

        res = ctypes.c_void_p()
        err = nvcuda.cuGraphicsD3D11RegisterResource(ctypes.byref(res), self._tex, 0)
        _check_cu(err, "cuGraphicsD3D11RegisterResource")
        self._cu_resource = res.value

    def _present_on_window_thread(self, dev_ptr, width, height):
        if self._closed or self._device is None or self._swapchain is None:
            return
        st = self._pending_status
        if st is not None:
            self._pending_status = None
            if self._status_hwnd:
                user32.SetWindowTextW(self._status_hwnd, st)
        self._pending_resize = None
        self._ensure_texture(width, height)
        if self._cu_event:
            _check_cu(nvcuda.cuStreamWaitEvent(None, self._cu_event, 0), "cuStreamWaitEvent")
        res_arr = (ctypes.c_void_p * 1)(self._cu_resource)
        _check_cu(nvcuda.cuGraphicsMapResources(1, res_arr, None), "MapResources")
        try:
            cu_array = ctypes.c_void_p()
            _check_cu(
                nvcuda.cuGraphicsSubResourceGetMappedArray(ctypes.byref(cu_array), self._cu_resource, 0, 0),
                "GetMappedArray",
            )
            cpy = CUDA_MEMCPY2D()
            cpy.srcMemoryType = CU_MEMORYTYPE_DEVICE
            cpy.srcDevice = int(dev_ptr)
            cpy.srcPitch = width * 4
            cpy.dstMemoryType = CU_MEMORYTYPE_ARRAY
            cpy.dstArray = cu_array.value
            cpy.WidthInBytes = width * 4
            cpy.Height = height
            _check_cu(nvcuda.cuMemcpy2DAsync_v2(ctypes.byref(cpy), None), "Memcpy2DAsync")
        finally:
            nvcuda.cuGraphicsUnmapResources(1, res_arr, None)

        backbuffer = ctypes.c_void_p()
        hr = _com_call(
            self._swapchain, 9, ctypes.c_long,
            [wt.UINT, ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p)],
            0, ctypes.byref(IID_ID3D11Texture2D), ctypes.byref(backbuffer),
        )
        if hr == 0 and backbuffer.value:
            bb_desc = D3D11_TEXTURE2D_DESC()
            _com_call(
                backbuffer.value, 10, None,
                [ctypes.POINTER(D3D11_TEXTURE2D_DESC)],
                ctypes.byref(bb_desc),
            )
            if int(bb_desc.Width) != int(width) or int(bb_desc.Height) != int(height):
                _com_release(backbuffer.value)
                backbuffer = ctypes.c_void_p()
                _com_call(
                    self._swapchain, 13, ctypes.c_long,
                    [wt.UINT, wt.UINT, wt.UINT, wt.UINT, wt.UINT],
                    0, int(width), int(height), DXGI_FORMAT_R8G8B8A8_UNORM, 0,
                )
                hr = _com_call(
                    self._swapchain, 9, ctypes.c_long,
                    [wt.UINT, ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p)],
                    0, ctypes.byref(IID_ID3D11Texture2D), ctypes.byref(backbuffer),
                )
                if hr == 0 and backbuffer.value:
                    _com_call(
                        backbuffer.value, 10, None,
                        [ctypes.POINTER(D3D11_TEXTURE2D_DESC)],
                        ctypes.byref(bb_desc),
                    )
        if hr == 0 and backbuffer.value:
            copy_w = min(int(width), int(bb_desc.Width))
            copy_h = min(int(height), int(bb_desc.Height))
            if copy_w > 0 and copy_h > 0:
                src_box = D3D11_BOX(0, 0, 0, copy_w, copy_h, 1)
                _com_call(
                    self._context, 46, None,
                    [ctypes.c_void_p, wt.UINT, wt.UINT, wt.UINT, wt.UINT, ctypes.c_void_p, wt.UINT, ctypes.POINTER(D3D11_BOX)],
                    backbuffer.value, 0, 0, 0, 0, self._tex, 0, ctypes.byref(src_box),
                )
            _com_release(backbuffer.value)

        _com_call(self._swapchain, 8, ctypes.c_long, [wt.UINT, wt.UINT], 0, 0)

    def _cleanup_d3d11(self):
        if self._cu_resource is not None:
            try:
                nvcuda.cuGraphicsUnregisterResource(self._cu_resource)
            except Exception:
                pass
            self._cu_resource = None
        if self._tex is not None:
            _com_release(self._tex)
            self._tex = None
        if self._swapchain is not None:
            _com_release(self._swapchain)
            self._swapchain = None
        if self._context is not None:
            _com_release(self._context)
            self._context = None
        if self._device is not None:
            _com_release(self._device)
            self._device = None

    def present_cuda(self, dev_ptr, width, height, cuda_stream=0):
        """Copy an (H, W, 4) uint8 CUDA device buffer into the D3D11 swapchain and Present."""
        canvas_hwnd = self._canvas_hwnd
        if self._closed or not canvas_hwnd:
            return False
        if self._cu_event:
            _check_cu(
                nvcuda.cuEventRecord(self._cu_event, ctypes.c_void_p(cuda_stream)),
                "cuEventRecord",
            )
        self._present_done.clear()
        self._present_error = None
        self._pending_frame = (int(dev_ptr), int(width), int(height))
        if not user32.PostMessageW(canvas_hwnd, WM_APP_PRESENT, 0, 0):
            return False
        self._present_done.wait(timeout=2.0)
        if self._present_error is not None:
            raise self._present_error
        return not self._closed

    def close(self):
        hwnd = self._hwnd
        self._closed = True
        self._present_done.set()
        if hwnd:
            user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
        t = self._thread
        if t is not None and t.is_alive():
            t.join(timeout=2.0)
        self._thread = None
        if self._cu_event is not None:
            try:
                nvcuda.cuEventDestroy_v2(self._cu_event)
            except Exception:
                pass
            self._cu_event = None
