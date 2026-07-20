# -*- mode: python ; coding: utf-8 -*-
# SixTerminal.spec — PyInstaller build spec for Six-Terminal
#
# Build command (run from C:\SixTerminal-Live):
#   pyinstaller SixTerminal.spec
#
# Output: dist\SixTerminal.exe  (~80-150 MB, standalone Windows executable)

block_cipher = None

a = Analysis(
    ['launch.py'],
    pathex=['.'],
    binaries=[],
    datas=[
        # Bundle HTML templates so Flask can find them inside the exe
        ('ui/templates', 'ui/templates'),
    ],
    hiddenimports=[
        # Project modules (PyInstaller may miss dynamic imports)
        'engine',
        'engine.schedule_model',
        'engine.edit_engine',
        'engine.xer_reader',
        'engine.xml_reader',
        'engine.xml_writer',
        'interpreter',
        'interpreter.llm_interpreter',
        # Flask internals
        'flask',
        'flask.templating',
        'jinja2',
        'jinja2.ext',
        'werkzeug',
        'werkzeug.routing',
        'werkzeug.serving',
        # HTTP / networking
        'anthropic',
        'openai',
        'httpx',
        'httpcore',
        'anyio',
        # XML parsing
        'xml.etree.ElementTree',
        'lxml',
        # Standard lib extras sometimes missed
        'email.mime.multipart',
        'email.mime.text',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # Exclude heavy packages not used by this app to keep exe smaller
    excludes=[
        'tkinter', 'matplotlib', 'numpy', 'pandas', 'scipy',
        'PIL', 'cv2', 'torch', 'tensorflow',
        'IPython', 'jupyter', 'notebook',
        'PyQt5', 'PyQt6', 'wx',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='SixTerminal',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    # console=True keeps the terminal window so testers can see server status
    # and any error messages. Change to False for a clean no-window build.
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
