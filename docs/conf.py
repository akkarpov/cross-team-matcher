"""Sphinx documentation configuration; imports never connect to PostgreSQL."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
project = "Cross-Team Matcher"
author = "Карпов А. К., Лебедев М. О., Кречун А. В."
copyright = "2026, МИЭМ НИУ ВШЭ"
release = "4.1.0"
language = "ru"
extensions = ["sphinx.ext.autodoc", "sphinx.ext.napoleon", "sphinx.ext.viewcode"]
html_theme = "alabaster"
html_title = "Cross-Team Matcher — документация"
html_favicon = "../report/favicon.svg"
html_theme_options = {"description": "Распределённый подбор проектных команд", "fixed_sidebar": True}
exclude_patterns = ["_build", "generated/doxygen", "protocol.md", "assessment.md"]
autodoc_member_order = "bysource"
nitpicky = False

