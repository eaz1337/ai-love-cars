import os
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for folder in ("scrapers", "analysis"):
    sys.path.insert(0, os.path.join(ROOT, folder))


def _stub_if_missing(name, **attrs):
    """Scrapery importują DrissionPage i curl_cffi na górze pliku. Testy nie robią żadnych requestów,
    więc gdy paczek brakuje, podstawiamy puste atrapy."""
    try:
        __import__(name)
    except ImportError:
        module = types.ModuleType(name)
        for key, value in attrs.items():
            setattr(module, key, value)
        sys.modules[name] = module
        return module


_stub_if_missing("DrissionPage", ChromiumPage=object, ChromiumOptions=object)
curl_cffi = _stub_if_missing("curl_cffi")
if curl_cffi is not None:
    requests_stub = types.ModuleType("curl_cffi.requests")
    sys.modules["curl_cffi.requests"] = requests_stub
    curl_cffi.requests = requests_stub
