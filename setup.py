from setuptools import setup, find_packages

with open("README.md", "r") as fh:
    long_description = fh.read()

setup(
    name="openapi-httpx-client",
    version="0.4.1",
    author="lloydzhou",
    author_email="lloydzhou@qq.com",
    description="A Python client for OpenAPI specifications using httpx",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/lloydzhou/openapiclient",
    packages=find_packages(),
    install_requires=[
        "httpx>=0.23.0",
        "pyyaml>=6.0",
    ],
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
    ],
    python_requires=">=3.7",
)

