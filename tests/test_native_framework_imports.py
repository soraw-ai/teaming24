from __future__ import annotations

import importlib


def test_native_framework_modules_import_without_litellm() -> None:
    importlib.import_module("teaming24.agent.framework.native.runtime")
    importlib.import_module("teaming24.agent.framework.native.runner")
    importlib.import_module("teaming24.agent.framework.native.adapter")
