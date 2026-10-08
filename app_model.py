"""Manage the user's local model separately from application updates."""
import os
import shutil
import tempfile
from pathlib import Path


def managed_model(project):
    return Path(project) / 'models' / 'best.pt'


def import_model(source, project):
    """Copy a selected weight atomically, preserving the previous one for rollback."""
    source = Path(source).resolve(strict=True)
    target = managed_model(project)
    if not source.is_file() or source.suffix.lower() != '.pt' or source.stat().st_size == 0:
        raise ValueError('Select a nonempty .pt model file')
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and source.samefile(target):
        return target

    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=target.parent, suffix='.partial', delete=False) as output:
            temporary = Path(output.name)
            with source.open('rb') as input_file:
                shutil.copyfileobj(input_file, output, length=1024 * 1024)
            output.flush()
            os.fsync(output.fileno())
        if temporary.stat().st_size != source.stat().st_size:
            raise IOError('Model copy size differs from source')
        if target.is_file():
            previous = target.with_name('best.previous.pt')
            backup = previous.with_suffix('.partial')
            try:
                shutil.copy2(target, backup)
                os.replace(backup, previous)
            finally:
                backup.unlink(missing_ok=True)
        os.replace(temporary, target)
        return target
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
