from setuptools import setup, find_packages

setup(
    name="elysia-ai-client",
    version="1.0.0",
    description="Elysia AI Chat Client for Raspberry Pi",
    author="ZJY",
    packages=find_packages(),
    py_modules=["elysia_client", "elysia_cli"],
    install_requires=[
        "websocket-client>=1.6.0",
    ],
    entry_points={
        "console_scripts": [
            "elysia-cli=elysia_cli:main",
            "elysia-gui=elysia_client:main",
        ],
    },
    python_requires=">=3.7",
)