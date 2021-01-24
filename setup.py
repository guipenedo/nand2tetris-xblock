"""Setup for python judge XBlock."""

import os
from setuptools import setup


def package_data(pkg, roots):
    """Generic function to find package_data.

    All of the files under each of the `roots` will be declared as package
    data for package `pkg`.

    """
    data = []
    for root in roots:
        for dirname, _, files in os.walk(os.path.join(pkg, root)):
            for fname in files:
                data.append(os.path.relpath(os.path.join(dirname, fname), pkg))

    return {pkg: data}


setup(
    name='nand2tetris-xblock',
    version='0.1',
    description='xblock to evaluate students nand2tetris submissions',
    packages=[
        'nand2tetris',
    ],
    install_requires=[
        'XBlock', 'epicbox', 'xblock-utils', 'edx-submissions'
    ],
    dependency_links=[
        'git+https://github.com/StepicOrg/epicbox.git',
    ],
    entry_points={
        'xblock.v1': [
            'nand2tetris = nand2tetris:Nand2TetrisXBlock',
        ]
    },
    package_data=package_data("nand2tetris", ["static", "templates"]),
)
