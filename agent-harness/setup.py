from setuptools import find_namespace_packages, setup

setup(name="cli-anything-flstudio", version="0.2.0",
      packages=find_namespace_packages(include=["cli_anything.*"]),
      install_requires=["click>=8", "fl-studio-mcp"],
      entry_points={"console_scripts": ["syncfl=cli_anything.flstudio.flstudio_cli:cli",
                                        "cli-anything-flstudio=cli_anything.flstudio.flstudio_cli:cli"]})
