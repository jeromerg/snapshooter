import setuptools

with open("README.md", "r") as fh:
    long_description = fh.read()

setuptools.setup(
    name="snapshooter",
    version="0.0.28",
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
    ],
    python_requires='>=3.10',
)
