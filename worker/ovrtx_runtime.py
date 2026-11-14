"""Discovery and status reporting for the standalone NVIDIA Omniverse RTX runtime."""
import json
import os
from pathlib import Path


def discover_ovrtx_runtime():
    """Inspects the ovrtx installation manifest and returns capability status."""
    root = Path(os.environ.get('ORCHFLOW_OVRTX_ROOT', 'I:/AI/OrchFlow-renderers/ovrtx'))
    manifest_path = root / 'runtime.json'
    status = {
        'id': 'OmniverseRTX',
        'name': 'Omniverse RTX (NVIDIA ovrtx)',
        'installed': False,
        'available': False,
        'path': str(root),
        'features': [],
        'message': 'Omniverse RTX is not installed. Run scripts/setup-ovrtx.ps1 to install.'
    }

    if not manifest_path.is_file():
        return status

    status['installed'] = True
    try:
        data = json.loads(manifest_path.read_text(encoding='utf-8-sig'))
        python_exe = data.get('pythonExecutable')

        # Fallback to local .venv-ovrtx if pythonExecutable not set or relocated
        if not python_exe or not os.path.isfile(python_exe):
            repo_venv = Path(__file__).resolve().parents[1] / '.venv-ovrtx/Scripts/python.exe'
            if repo_venv.is_file():
                python_exe = str(repo_venv)

        if not python_exe or not os.path.isfile(python_exe):
            status['message'] = 'Omniverse RTX Python environment not found. Re-run scripts/setup-ovrtx.ps1.'
            return status

        status.update({
            'available': bool(data.get('available', True)),
            'ovrtxVersion': data.get('ovrtxVersion', 'unknown'),
            'ovstageVersion': data.get('ovstageVersion', 'unknown'),
            'pythonExecutable': python_exe,
            'device': data.get('device', 'NVIDIA GPU'),
            'driverVersion': data.get('driverVersion', 'unknown'),
            'features': data.get('features', []),
            'message': 'Installation found; scene rendering and material compatibility depend on runtime validation.'
        })
    except Exception as error:
        status['message'] = f'Failed reading ovrtx manifest: {error}'

    return status
