from setuptools import setup, find_packages

setup(
    name="superinstance-streamer",
    version="0.1.0",
    description="Audio streaming muxer with HLS, crossfades, and scheduling",
    long_description=open("README.md").read(),
    long_description_content_type="text/markdown",
    author="Lucineer / Casey DiGenaro",
    author_email="casey@superinstance.com",
    url="https://github.com/superinstance/streamer",
    license="MIT",
    packages=find_packages(where="src"),
    package_dir={"": "src"},
    python_requires=">=3.10",
    install_requires=[
        "pydub>=0.25",
    ],
    extras_require={
        "config": ["pyyaml>=6.0"],
        "dev": ["pytest>=7.0", "pyyaml>=6.0"],
    },
    entry_points={
        "console_scripts": [
            "luciddreamer-stream = streamer.stream_server:main",
        ],
    },
    classifiers=[
        "Development Status :: 4 - Beta",
        "License :: OSI Approved :: MIT License",
        "Programming Language :: Python :: 3",
        "Topic :: Multimedia :: Sound/Audio",
    ],
)
