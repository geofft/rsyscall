"The runtime functionality to actually use Nix dependencies"
import importlib.resources
from dataclasses import dataclass
from pathlib import Path
import typing as t
import json

__all__ = [
    'PackageClosure',
    'import_nixdep',
]

@dataclass
class PackageClosure:
    path: Path
    closure: t.List[Path]

_imported_nixdeps: t.Dict[t.Tuple[str, str], PackageClosure] = {}

def import_nixdep(module: str, name: str) -> PackageClosure:
    """Import a Nix dependency as specified by nixdeps at setuptools build time

    With import_nixdep, you can import a Nix dependency and learn its path in a relatively
    lightweight way. Further, you know the closure for the dependency: this means you can
    deploy this dependency to arbitrary places, without necessarily having access to Nix
    tools or the Nix store.

    See the nixdep.setuptools module docstring for more about how to use nixdeps in your
    setuptools setup.py.

    """
    if (module, name) in _imported_nixdeps:
        return _imported_nixdeps[(module, name)]
    text = importlib.resources.read_text(module, name + '.json')
    data = json.loads(text)
    path = Path(data["path"])
    closure = [Path(elem) for elem in data["closure"]]
    nixdep = PackageClosure(path, closure)
    _imported_nixdeps[(module, name)] = nixdep
    return nixdep
