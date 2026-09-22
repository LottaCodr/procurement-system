"""The Vercel build must not depend on a pre-set DJANGO_SETTINGS_MODULE.

Regression for the deploy that died in build.py:

    ImproperlyConfigured: Requested setting LOGGING_CONFIG, but settings are
    not configured.

``os.environ.setdefault`` does not replace a blank value, and a blank
``DJANGO_SETTINGS_MODULE`` is what Django means by "not configured". ``.env``
is not on the builder (``.vercelignore``). The stylesheet step must still
finish, and ``manage.py`` / ASGI must still be able to boot.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_prepare_replaces_a_blank_settings_module(monkeypatch):
    from config.bootstrap import SETTINGS_MODULE, prepare

    monkeypatch.setenv("DJANGO_SETTINGS_MODULE", "")
    assert prepare() == SETTINGS_MODULE
    assert os.environ["DJANGO_SETTINGS_MODULE"] == SETTINGS_MODULE


def test_prepare_replaces_whitespace_and_preserves_a_real_module(monkeypatch):
    from config.bootstrap import SETTINGS_MODULE, prepare

    monkeypatch.setenv("DJANGO_SETTINGS_MODULE", "   ")
    assert prepare() == SETTINGS_MODULE

    # Vercel collectstatic points this at a temporary shim. Do not clobber it.
    monkeypatch.setenv("DJANGO_SETTINGS_MODULE", "_vercel_collectstatic_settings")
    assert prepare() == "_vercel_collectstatic_settings"


def test_css_build_does_not_import_django_settings():
    """Importing the builder must not touch django.conf.settings.

    The Vercel fallback used to call django.setup() just to resolve a path.
    That is the call that raised LOGGING_CONFIG.
    """
    env = os.environ.copy()
    env["DJANGO_SETTINGS_MODULE"] = ""
    probe = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; sys.path.insert(0, 'apps'); "
            "from core import css_build; "
            "css = css_build.build_css(); "
            "assert css and '{' in css; "
            "print(len(css))",
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert probe.returncode == 0, probe.stderr


def test_build_py_survives_a_blank_settings_module():
    """The exact builder environment that failed on Vercel."""
    env = os.environ.copy()
    env["DJANGO_SETTINGS_MODULE"] = ""
    env.pop("VERCEL_ENV", None)
    env.pop("DATABASE_URL", None)
    result = subprocess.run(
        [sys.executable, "build.py"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "resolved='config.settings'" in result.stdout
    assert (ROOT / "apps" / "core" / "static" / "css" / "app.css").is_file()


def test_asgi_boots_with_a_blank_settings_module():
    """Vercel serves config.asgi:application. A blank dashboard value must not 500 every request."""
    env = os.environ.copy()
    env["DJANGO_SETTINGS_MODULE"] = ""
    env.pop("VERCEL_ENV", None)
    env.pop("DATABASE_URL", None)
    probe = subprocess.run(
        [
            sys.executable,
            "-c",
            "from config.asgi import application; print(type(application).__name__)",
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert probe.returncode == 0, probe.stderr
    assert "ASGI" in probe.stdout or "application" in probe.stdout.lower()


def test_production_vercel_without_a_database_refuses_to_boot():
    env = os.environ.copy()
    env["VERCEL_ENV"] = "production"
    env["DJANGO_SECRET_KEY"] = "not-the-default"
    env["USE_POSTGRES"] = "0"
    env.pop("DATABASE_URL", None)
    probe = subprocess.run(
        [sys.executable, "-c", "import config.settings"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert probe.returncode != 0
    assert "DATABASE_URL" in probe.stderr


def test_manage_py_check_survives_a_blank_settings_module():
    env = os.environ.copy()
    env["DJANGO_SETTINGS_MODULE"] = ""
    env.pop("VERCEL_ENV", None)
    result = subprocess.run(
        [sys.executable, "manage.py", "check"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
