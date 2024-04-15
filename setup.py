import os
import setuptools

with open("README.md", "r") as fh:
    long_description = fh.read()

version = os.getenv("VERSION")

setuptools.setup(
    name="snapshooter",
    version=version,
    author="jeromerg",
    author_email="jeromerg@gmx.net",
    description="Provides a set of utilities for comparing and backing up data on different filesystems",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/jeromerg/snapshooter",
    packages=setuptools.find_packages(),
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
    ],
    install_requires=[
        'fsspec',
        'pandas',
        'tabulate',
        'typer[all]',
        'tzlocal',
    ],
    entry_points={
        'console_scripts': [
            'snapshooter=snapshooter.cli:main_cli',
        ],
    },
    python_requires='>=3.10',
)
