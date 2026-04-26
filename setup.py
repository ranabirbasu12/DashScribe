"""
py2app build script for DashScribe.

Usage:
    python setup.py py2app          # Full standalone build
    python setup.py py2app -A       # Alias mode (development, links to source)

Output: dist/DashScribe.app
"""
import re
from setuptools import setup

# Read version from version.py without importing the module
# (avoids importing app dependencies during build)
with open("version.py") as _vf:
    _VERSION = re.search(r'__version__\s*=\s*["\']([^"\']+)', _vf.read()).group(1)

try:
    # Prevent py2app recipe from force-including the entire scipy tree.
    from py2app.recipes import autopackages as _autopackages

    _autopackages.AUTO_PACKAGES = [
        pkg for pkg in _autopackages.AUTO_PACKAGES if pkg != "scipy"
    ]
except Exception:
    pass

APP = ['main.py']

DATA_FILES = [
    ('static', [
        'static/index.html',
        'static/style.css',
        'static/app.js',
        'static/file.js',
        'static/bar.html',
        'static/bar.css',
        'static/bar.js',
        'static/classnote.js',
        'static/meeting.js',
    ]),
]

OPTIONS = {
    'argv_emulation': False,
    'iconfile': 'DashScribe.icns',
    'includes': [
        # Project modules (some are dynamically imported)
        'app', 'recorder', 'transcriber', 'clipboard', 'hotkey',
        'state', 'history', 'config', 'internal_clipboard', 'system_audio', 'aec',
        'vad', 'pipeline', 'permissions', 'version', 'updater', 'llm', 'formatter', 'context',
        'diarizer_pyannote', 'parakeet_transcriber',
        # PyObjC frameworks
        'objc', 'Quartz', 'AppKit', 'Foundation', 'WebKit',
        'CoreMedia', 'ScreenCaptureKit', 'PyObjCTools', 'AVFoundation',
        # sounddevice
        'sounddevice', '_sounddevice_data', '_cffi_backend',
        # FastAPI/uvicorn stack
        'fastapi', 'uvicorn', 'uvicorn.logging', 'uvicorn.loops',
        'uvicorn.loops.auto', 'uvicorn.protocols',
        'uvicorn.protocols.http', 'uvicorn.protocols.http.auto',
        'uvicorn.protocols.websockets', 'uvicorn.protocols.websockets.auto',
        'uvicorn.lifespan', 'uvicorn.lifespan.on',
        'starlette', 'websockets',
        'anyio', 'anyio._backends._asyncio',
        # ML stack
        'mlx', 'mlx_whisper', 'mlx_lm', 'onnxruntime',
        'transformers', 'sentencepiece', 'jinja2',
        'safetensors', 'tokenizers',
        'numpy', 'scipy.io', 'scipy.io.wavfile',
        # huggingface_hub and transitive deps
        'huggingface_hub', 'tiktoken', 'regex',
        'httpx', 'httpcore', 'h11',
        'filelock', 'fsspec', 'tqdm', 'pyyaml', 'yaml',
        'packaging', 'more_itertools',
        # Other dependencies
        'pyperclip', 'pydantic', 'pydantic_core',
        'typing_extensions', 'annotated_types',
        # stdlib modules needed by mlx_lm / transformers for model loading
        # (safetensors and transformers require these for weight deserialization)
        'pickletools', '_compat_pickle',
    ],
    'packages': [
        'mlx_whisper', 'mlx_lm', 'transformers', 'sentencepiece',
        'jinja2', 'safetensors', 'tokenizers',
        'numpy', 'scipy.io',
        'huggingface_hub', 'tiktoken', 'pydantic', 'pydantic_core',
        'httpx', 'httpcore',
        'filelock', 'fsspec', 'tqdm', 'yaml',
        'packaging', 'more_itertools',
        'webview', 'uvicorn', 'fastapi', 'starlette', 'anyio',
        'certifi',
        # sounddevice + native PortAudio dylib (must not be zipped)
        'sounddevice', '_sounddevice_data',
        # ONNX Runtime for VAD (has native libraries, must not be zipped)
        'onnxruntime',
        # regex has native C extension + Python files — must not be zipped
        'regex',
        # markupsafe has native C extension needed by jinja2
        'markupsafe',
        # PyObjC (mlx and PyObjCTools are namespace packages — handled separately)
        'objc', 'Quartz', 'AppKit', 'Foundation', 'WebKit',
        'CoreMedia', 'CoreFoundation', 'ScreenCaptureKit', 'AVFoundation',
        # File transcription feature deps (native binaries — must not be zipped)
        'sherpa_onnx',
        'parakeet_mlx',
        'yt_dlp',
        'docx',
        'soundfile',
    ],
    'excludes': [
        # torch is 490MB+ and only referenced by mlx_whisper/torch_whisper.py
        # which is dead code (never imported by any mlx_whisper module)
        'torch', 'torchgen', 'functorch',
        'sympy', 'networkx', 'mpmath',
        # Genuinely unused at runtime
        'matplotlib', 'PIL',
        'IPython', 'jupyter', 'notebook',
        'pytest', 'pytest_asyncio', 'pynput',
        # Prune heavy/unusable subtrees that cause py2app graph blow-ups.
        'test', 'tests',
        'numpy.tests', 'numpy.testing.tests',
        'scipy.tests', 'scipy._lib.tests',
        'numba.tests', 'numba.cuda',
        'llvmlite.tests',
        'onnxruntime.tools', 'onnxruntime.training',
        'setuptools.tests', 'setuptools._distutils.tests',
        # Non-mac backends
        'webview.platforms.winforms', 'webview.platforms.cef', 'webview.platforms.mshtml',
        'click._winconsole',
        # Avoid tkinter recipe aborts in headless build environments.
        '_tkinter', 'tkinter', 'Tkinter',
    ],
    'plist': {
        'CFBundleName': 'DashScribe',
        'CFBundleDisplayName': 'DashScribe',
        'CFBundleIdentifier': 'com.dashscribe.app',
        'CFBundleVersion': _VERSION,
        'CFBundleShortVersionString': _VERSION,
        'LSMinimumSystemVersion': '14.0',
        'LSArchitecturePriority': ['arm64'],
        'NSHighResolutionCapable': True,
        'NSMicrophoneUsageDescription':
            'DashScribe needs microphone access to record and transcribe your speech.',
        'NSScreenCaptureUsageDescription':
            'DashScribe uses system audio capture for echo cancellation '
            'to improve transcription accuracy when audio is playing.',
        'NSAppTransportSecurity': {
            'NSAllowsLocalNetworking': True,
        },
    },
    'emulate_shell_environment': True,
    'semi_standalone': False,
    'site_packages': True,
    'strip': False,
    'arch': 'arm64',
}

setup(
    name='DashScribe',
    app=APP,
    data_files=DATA_FILES,
    options={'py2app': OPTIONS},
    setup_requires=['py2app'],
)
