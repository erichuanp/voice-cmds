"""Minimal ONNX Runtime C-API binding via ctypes.

The embedder used to pull in the whole `onnxruntime` Python package
(~35 MB of .pyd + DLL inside the frozen bundle). sherpa-onnx already ships
its own onnxruntime.dll for STT (sherpa_onnx/lib/onnxruntime.dll), so this
module drives that same DLL through the official C API and lets the
embedder drop the Python package entirely.

The OrtApi indices below are the ordered struct entries of
include/onnxruntime/core/session/onnxruntime_c_api.h **v1.24.4**
(ORT_API_VERSION 24), counted after expanding the ORT_CLASS_RELEASE /
ORT_API_T macros. The C API is append-only, so indices are stable for a
given ORT release; this module asserts the DLL is that release.
"""
from __future__ import annotations

import ctypes
import logging
import sys
from pathlib import Path

logger = logging.getLogger("voice_cmds.ort_ffi")

ORT_API_VERSION = 24

# Library name and path-char width differ per platform:
#   Windows: sherpa_onnx/lib/onnxruntime.dll, ORTCHAR_T = wchar_t
#   macOS:   sherpa_onnx/lib/libonnxruntime.dylib, ORTCHAR_T = char
if sys.platform == "darwin":
    _ORT_DLL_NAME = "libonnxruntime.dylib"
    _MODEL_PATH_TYPE = ctypes.c_char_p
else:
    _ORT_DLL_NAME = "onnxruntime.dll"
    _MODEL_PATH_TYPE = ctypes.c_wchar_p

# OrtApi struct field indices (0-based), onnxruntime_c_api.h v1.24.4
_IDX = {
    "GetErrorCode": 1,
    "GetErrorMessage": 2,
    "CreateEnv": 3,
    "CreateSession": 7,
    "Run": 9,
    "CreateSessionOptions": 10,
    "SetSessionGraphOptimizationLevel": 23,
    "SetIntraOpNumThreads": 24,
    "CreateTensorWithDataAsOrtValue": 49,
    "GetTensorMutableData": 51,
    "GetTensorShapeElementCount": 64,
    "GetTensorTypeAndShape": 65,
    "CreateCpuMemoryInfo": 69,
    "ReleaseEnv": 92,
    "ReleaseStatus": 93,
    "ReleaseMemoryInfo": 94,
    "ReleaseSession": 95,
    "ReleaseValue": 96,
    "ReleaseTypeInfo": 98,
    "ReleaseTensorTypeAndShapeInfo": 99,
    "ReleaseSessionOptions": 100,
}
_N_FIELDS = max(_IDX.values()) + 1

# Enums used below (onnxruntime_c_api.h)
_ORT_LOGGING_LEVEL_WARNING = 2
_ORT_ENABLE_ALL = 99  # GraphOptimizationLevel
_ONNX_TENSOR_ELEMENT_DATA_TYPE_INT64 = 7
_ORT_ARENA_ALLOCATOR = 1  # OrtAllocatorType
_ORT_MEM_TYPE_DEFAULT = 0  # OrtMemTypeCPUInput


class _OrtApi(ctypes.Structure):
    """All fields are function pointers, so c_void_p keeps offsets exact."""

    _fields_ = [(f"f{i}", ctypes.c_void_p) for i in range(_N_FIELDS)]


def _check(status_ptr, api) -> None:
    """Raise RuntimeError with ORT's message if status is non-NULL."""
    if not status_ptr:
        return
    get_code = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p)(
        getattr(api, f"f{_IDX['GetErrorCode']}")
    )
    get_msg = ctypes.CFUNCTYPE(ctypes.c_char_p, ctypes.c_void_p)(
        getattr(api, f"f{_IDX['GetErrorMessage']}")
    )
    release = ctypes.CFUNCTYPE(None, ctypes.c_void_p)(
        getattr(api, f"f{_IDX['ReleaseStatus']}")
    )
    code = get_code(status_ptr)
    msg = get_msg(status_ptr)
    text = msg.decode("utf-8", "replace") if msg else "<no message>"
    release(status_ptr)
    raise RuntimeError(f"onnxruntime error {code}: {text}")


def _load_api() -> tuple[ctypes.CDLL, _OrtApi]:
    """Locate sherpa-onnx's bundled onnxruntime and resolve the OrtApi table."""
    import sherpa_onnx

    dll_path = Path(sherpa_onnx.__file__).parent / "lib" / _ORT_DLL_NAME
    if not dll_path.exists():
        raise RuntimeError(
            f"未找到 sherpa_onnx 自带的 onnxruntime 运行库: {dll_path}"
        )
    dll = ctypes.CDLL(str(dll_path))
    dll.OrtGetApiBase.restype = ctypes.c_void_p
    base = dll.OrtGetApiBase()
    if not base:
        raise RuntimeError("OrtGetApiBase() returned NULL")
    # OrtApiBase::GetApi is the first member of the struct
    get_api_ptr = ctypes.cast(base, ctypes.POINTER(ctypes.c_void_p))[0]
    get_api = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_uint32)(get_api_ptr)
    api_ptr = get_api(ORT_API_VERSION)
    if not api_ptr:
        raise RuntimeError(
            f"onnxruntime.dll 不支持 ORT API v{ORT_API_VERSION}，"
            "请更新 sherpa_onnx"
        )
    return dll, ctypes.cast(api_ptr, ctypes.POINTER(_OrtApi)).contents


class ORTSession:
    """A tiny CPU InferenceSession for a single model (BGE embedder).

    run() feeds int64 input_ids/attention_mask/token_type_ids and returns
    the flat float32 buffer of the "last_hidden_state" output. Callers are
    embedder.py only.
    """

    _INPUT_NAMES = ("input_ids", "attention_mask", "token_type_ids")
    _OUTPUT_NAMES = ("last_hidden_state",)

    def __init__(self, model_path: str, intra_threads: int = 2) -> None:
        self._dll, api = _load_api()
        self._api = api

        def fn(name, restype, *argtypes):
            raw = getattr(api, f"f{_IDX[name]}")
            return ctypes.CFUNCTYPE(restype, *argtypes)(raw)

        self._create_env = fn(
            "CreateEnv", ctypes.c_void_p,
            ctypes.c_int, ctypes.c_char_p, ctypes.POINTER(ctypes.c_void_p),
        )
        self._create_session_options = fn(
            "CreateSessionOptions", ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)
        )
        self._set_graph_opt = fn(
            "SetSessionGraphOptimizationLevel", ctypes.c_void_p,
            ctypes.c_void_p, ctypes.c_int,
        )
        self._set_intra_threads = fn(
            "SetIntraOpNumThreads", ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int
        )
        self._create_session = fn(
            "CreateSession", ctypes.c_void_p,
            ctypes.c_void_p, _MODEL_PATH_TYPE, ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_void_p),
        )
        self._create_mem_info = fn(
            "CreateCpuMemoryInfo", ctypes.c_void_p,
            ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p),
        )
        self._create_tensor = fn(
            "CreateTensorWithDataAsOrtValue", ctypes.c_void_p,
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t,
            ctypes.POINTER(ctypes.c_int64), ctypes.c_size_t, ctypes.c_int,
            ctypes.POINTER(ctypes.c_void_p),
        )
        self._run = fn(
            "Run", ctypes.c_void_p,
            ctypes.c_void_p, ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_char_p), ctypes.POINTER(ctypes.c_void_p),
            ctypes.c_size_t, ctypes.POINTER(ctypes.c_char_p), ctypes.c_size_t,
            ctypes.POINTER(ctypes.c_void_p),
        )
        self._get_type_shape = fn(
            "GetTensorTypeAndShape", ctypes.c_void_p,
            ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p),
        )
        self._get_elem_count = fn(
            "GetTensorShapeElementCount", ctypes.c_void_p,
            ctypes.c_void_p, ctypes.POINTER(ctypes.c_size_t),
        )
        self._get_mutable_data = fn(
            "GetTensorMutableData", ctypes.c_void_p,
            ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p),
        )
        self._release = {}
        for name in ("ReleaseEnv", "ReleaseSessionOptions", "ReleaseSession",
                     "ReleaseMemoryInfo", "ReleaseValue", "ReleaseTypeInfo",
                     "ReleaseTensorTypeAndShapeInfo"):
            self._release[name] = fn(name, None, ctypes.c_void_p)

        self._env = ctypes.c_void_p()
        self._session = ctypes.c_void_p()
        self._meminfo = ctypes.c_void_p()
        # c_char_p wants bytes (c_wchar_p wants str) — normalize per platform.
        if _MODEL_PATH_TYPE is ctypes.c_char_p:
            model_path = str(model_path).encode("utf-8")
        try:
            _check(self._create_env(
                _ORT_LOGGING_LEVEL_WARNING, b"voice-cmds",
                ctypes.byref(self._env)), api)
            opts = ctypes.c_void_p()
            try:
                _check(self._create_session_options(ctypes.byref(opts)), api)
                self._set_graph_opt(opts, _ORT_ENABLE_ALL)
                self._set_intra_threads(opts, intra_threads)
                _check(self._create_session(
                    self._env, model_path, opts,
                    ctypes.byref(self._session)), api)
            finally:
                if opts:
                    self._release["ReleaseSessionOptions"](opts)
            _check(self._create_mem_info(
                _ORT_ARENA_ALLOCATOR, _ORT_MEM_TYPE_DEFAULT,
                ctypes.byref(self._meminfo)), api)
        except Exception:
            self.close()
            raise

    def run(self, ids: list[list[int]], mask: list[list[int]]) -> list[float]:
        """Run the model; ids/mask are rectangular (row-major, padded)."""
        n_rows = len(ids)
        max_len = len(ids[0]) if n_rows else 0
        total = n_rows * max_len
        if not total:
            return []

        buf_ids = (ctypes.c_int64 * total)()
        buf_mask = (ctypes.c_int64 * total)()
        buf_type = (ctypes.c_int64 * total)()  # zeros — token_type_ids
        for i in range(n_rows):
            row = ids[i]
            base = i * max_len
            for j, v in enumerate(row):
                buf_ids[base + j] = v
            for j, v in enumerate(mask[i]):
                buf_mask[base + j] = v

        shape = (ctypes.c_int64 * 2)(n_rows, max_len)
        values = (ctypes.c_void_p * 3)()
        for i, buf in enumerate((buf_ids, buf_mask, buf_type)):
            out = ctypes.c_void_p()
            _check(self._create_tensor(
                self._meminfo,
                ctypes.cast(buf, ctypes.c_void_p),
                ctypes.c_size_t(total * 8),
                shape, 2, _ONNX_TENSOR_ELEMENT_DATA_TYPE_INT64,
                ctypes.byref(out)), self._api)
            values[i] = out

        in_names = (ctypes.c_char_p * 3)(
            *(n.encode("utf-8") for n in self._INPUT_NAMES))
        out_names = (ctypes.c_char_p * 1)(
            self._OUTPUT_NAMES[0].encode("utf-8"))
        outputs = (ctypes.c_void_p * 1)()
        try:
            _check(self._run(
                self._session, None, in_names, values, 3, out_names, 1,
                outputs), self._api)
            out_value = outputs[0]
            info = ctypes.c_void_p()
            _check(self._get_type_shape(out_value, ctypes.byref(info)),
                   self._api)
            try:
                count = ctypes.c_size_t()
                _check(self._get_elem_count(info, ctypes.byref(count)),
                       self._api)
                data = ctypes.c_void_p()
                _check(self._get_mutable_data(out_value, ctypes.byref(data)),
                       self._api)
                floats = ctypes.cast(
                    data, ctypes.POINTER(ctypes.c_float))
                return floats[: count.value]
            finally:
                if info:
                    # GetTensorTypeAndShape returns an OrtTensorTypeAndShapeInfo,
                    # a different type from OrtTypeInfo in ORT 1.24.
                    self._release["ReleaseTensorTypeAndShapeInfo"](info)
        finally:
            for v in values:
                if v:
                    self._release["ReleaseValue"](v)

    def close(self) -> None:
        for handle, name in ((self._session, "ReleaseSession"),
                             (self._meminfo, "ReleaseMemoryInfo"),
                             (self._env, "ReleaseEnv")):
            if handle and hasattr(self, "_release") and name in self._release:
                self._release[name](handle)
        self._session = ctypes.c_void_p()
        self._meminfo = ctypes.c_void_p()
        self._env = ctypes.c_void_p()

    def __del__(self):  # pragma: no cover - best effort
        try:
            self.close()
        except Exception:
            pass
