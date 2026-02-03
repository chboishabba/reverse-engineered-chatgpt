from setuptools import find_packages, setup

setup(
    name="re_gpt",
    version="4.0.0",
    author="Zai-Kun",
    description="Unofficial reverse-engineered ChatGPT API in Python.",
    long_description=open("README.md").read(),
    long_description_content_type="text/markdown",
    url="https://github.com/Zai-Kun/reverse-engineered-chatgpt",
    project_urls={
        "Bug Tracker": "https://github.com/Zai-Kun/reverse-engineered-chatgpt/issues",
    },
    packages=find_packages(),
    install_requires=["curl_cffi==0.5.9", "websockets==12.0"],
    extras_require={
        "browser": ["playwright>=1.47"],
    },
    entry_points={
        "console_scripts": [
            "re-gpt = re_gpt.cli:main",
        ],
    },
)
