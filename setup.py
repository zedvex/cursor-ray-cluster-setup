from setuptools import setup, find_packages

setup(
    name="cursor-ray-cluster",
    version="0.1.0",
    description="Distributed computing environment for Cursor IDE with Ray",
    author="User",
    author_email="user@example.com",
    packages=find_packages(),
    install_requires=[
        "ray[default]>=2.0.0",
        "fastapi>=0.68.0",
        "uvicorn>=0.15.0",
        "psutil>=5.9.0",
        "requests>=2.26.0",
        "python-dotenv>=0.19.0",
        "numpy>=1.21.0",
        "pandas>=1.3.0",
        "anthropic>=0.5.0",
        "jinja2>=3.0.0",
    ],
    entry_points={
        "console_scripts": [
            "ray-start=cluster.ray_start:main",
            "ray-stop=cluster.ray_stop:main",
            "ray-monitor=cluster.monitor:main",
            "ray-linter=scripts.run_linter:main",
            "ray-indexer=scripts.run_indexer:main",
            "ray-formatter=scripts.run_formatter:main",
            "ray-tests=scripts.run_tests:main",
            "ray-proxy=scripts.start_ray_proxy:main",
        ],
    },
    python_requires=">=3.8",
)
