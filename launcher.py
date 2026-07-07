"""PyInstaller entry point.

The GUI code lives in the ``mtr_advanced`` package and uses relative
imports, so the frozen app must start from a top-level script that
imports it as a package — running ``mtr_advanced/main.py`` directly
would fail with "attempted relative import with no known parent
package".
"""

from mtr_advanced.main import main

if __name__ == "__main__":
    raise SystemExit(main())
