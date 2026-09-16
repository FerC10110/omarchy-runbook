"""Load bin/runbook (no .py extension) as the module `runbook_engine`."""
import importlib.machinery
import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ENGINE = os.path.join(os.path.dirname(HERE), "bin", "runbook")


def load():
    if "runbook_engine" in sys.modules:
        return sys.modules["runbook_engine"]
    loader = importlib.machinery.SourceFileLoader("runbook_engine", ENGINE)
    spec = importlib.util.spec_from_loader("runbook_engine", loader)
    module = importlib.util.module_from_spec(spec)
    sys.modules["runbook_engine"] = module
    loader.exec_module(module)
    return module
